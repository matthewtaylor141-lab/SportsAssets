/* BETTOR HEADQUARTERS · what every in-world screen on the floor shows.
 *
 * Pure canvas painters: each takes a 2D context, its size and window.BTHQ
 * (hq2-floor.js) and draws REAL values only -- the floor read, the equity
 * read, coverage and Xavier's recorded assessments -- each with its source
 * and timestamp. A missing value is drawn as UNAVAILABLE with its reason.
 * There is no random number, no synthetic price and no animated figure in
 * this file; the only motion on the floor's screens is the ticker texture
 * scrolling past the real lines painted here. PAPER and SMALL LIVE are
 * painted as separate blocks and never added together. */

const F = 'Inter, "Segoe UI", system-ui, -apple-system, sans-serif';
const M = 'ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace';
// the BettorToken lockup for in-world screens (local brand asset)
const BRAND_LOGO = (typeof Image !== 'undefined') ? Object.assign(new Image(), {src: 'brand/bettortoken-logo-white.png'}) : {complete: false};
export const COLORS = {ink: '#eaf1f8', soft: '#9db0c4', dim: '#5f7186', line: 'rgba(160,190,225,.14)', good: '#4fe0a8', warn: '#eab768', bad: '#ff7d8e', blue: '#68b6ff', gold: '#e9c46a'};
const C = COLORS;

/* ── helpers ──────────────────────────────────────────────────────── */
export function fit(ctx, text, maxW) {
  let t = String(text == null ? '' : text);
  if (ctx.measureText(t).width <= maxW) return t;
  while (t.length > 1 && ctx.measureText(t + '…').width > maxW) t = t.slice(0, -1);
  return t + '…';
}
function wrap(ctx, text, x, y, maxW, lh, maxLines) {
  const words = String(text || '').split(/\s+/);
  let line = '', n = 0;
  for (let i = 0; i < words.length; i++) {
    const test = line ? line + ' ' + words[i] : words[i];
    if (ctx.measureText(test).width > maxW && line) {
      if (n === maxLines - 1) { ctx.fillText(fit(ctx, line + ' ' + words.slice(i).join(' '), maxW), x, y + n * lh); return n + 1; }
      ctx.fillText(line, x, y + n * lh); n++; line = words[i];
    } else line = test;
  }
  if (line) { ctx.fillText(fit(ctx, line, maxW), x, y + n * lh); n++; }
  return n;
}
function rr(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}
function font(ctx, w, px, mono) { ctx.font = w + ' ' + Math.round(px) + 'px ' + (mono ? M : F); }
function text(ctx, s, x, y, color, w, px, mono, maxW, align) {
  font(ctx, w, px, mono); ctx.fillStyle = color; ctx.textAlign = align || 'left';
  ctx.fillText(maxW ? fit(ctx, s, maxW) : String(s), x, y); ctx.textAlign = 'left';
}
function chip(ctx, label, x, y, color, px, align) {
  font(ctx, '700', px, true);
  const w = ctx.measureText(label).width + px * 2.1, h = px * 1.9;
  const x0 = align === 'right' ? x - w : x;
  ctx.fillStyle = hexA(color, 0.16); rr(ctx, x0, y - h * 0.72, w, h, h / 2); ctx.fill();
  ctx.strokeStyle = hexA(color, 0.7); ctx.lineWidth = Math.max(1, px / 9); rr(ctx, x0, y - h * 0.72, w, h, h / 2); ctx.stroke();
  ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x0 + px * 0.85, y - h * 0.72 + h / 2, px * 0.3, 0, Math.PI * 2); ctx.fill();
  ctx.fillText(label, x0 + px * 1.45, y);
  return w;
}
export function hexA(hex, a) {
  const h = String(hex).replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
  return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
}
function bg(ctx, W, H, accent, opts) {
  const o = opts || {};
  const g = ctx.createLinearGradient(0, 0, 0, H);
  g.addColorStop(0, o.top || '#0a1422'); g.addColorStop(1, o.bottom || '#050a12');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  // a faint scan grid, fixed (not animated)
  ctx.strokeStyle = 'rgba(120,160,210,.035)'; ctx.lineWidth = 1;
  const step = Math.max(16, Math.round(W / 48));
  for (let x = step; x < W; x += step) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
  for (let y = step; y < H; y += step) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
  if (accent) { const a = ctx.createLinearGradient(0, 0, W, 0); a.addColorStop(0, accent); a.addColorStop(0.5, hexA(accent, 0.25)); a.addColorStop(1, 'rgba(0,0,0,0)'); ctx.fillStyle = a; ctx.fillRect(0, 0, W, Math.max(3, H / 110)); }
}
function header(ctx, W, H, title, sub, accent) {
  const s = H / 320;
  text(ctx, title.toUpperCase(), 18 * s, 34 * s, C.ink, '700', 15 * s, false, W * 0.62);
  if (sub) text(ctx, sub, W - 18 * s, 34 * s, accent || C.soft, '600', 11 * s, true, W * 0.36, 'right');
  ctx.fillStyle = C.line; ctx.fillRect(18 * s, 46 * s, W - 36 * s, Math.max(1, s));
}
function footer(ctx, W, H, s1) {
  const s = H / 320;
  ctx.fillStyle = C.line; ctx.fillRect(18 * s, H - 30 * s, W - 36 * s, Math.max(1, s));
  text(ctx, s1, 18 * s, H - 12 * s, C.dim, '500', 10 * s, true, W - 36 * s);
}
function na(ctx, W, H, why, y) {
  const s = H / 320;
  text(ctx, 'UNAVAILABLE', W / 2, (y || H / 2) - 4 * s, C.warn, '700', 22 * s, true, W - 40 * s, 'center');
  font(ctx, '500', 12 * s); ctx.fillStyle = C.soft; ctx.textAlign = 'center';
  wrap(ctx, why || 'no value recorded', W / 2, (y || H / 2) + 24 * s, W - 60 * s, 16 * s, 2); ctx.textAlign = 'left';
}
const short = (m) => String(m || '').replace(/^aec-/, '').replace(/-20\d\d-\d\d-\d\d$/, '').toUpperCase();
const stColor = (s) => /^(OK|HEALTHY|CURRENT|MARKED|ENTER)$/.test(s) ? C.good : /STALE|WAIT|REFUSING|SHADOW|UNSUPPORTED|NOT_CONFIGURED|READY/.test(s) ? C.warn : /UNAVAILABLE|INCIDENT|FAIL|STOP|DEGRADED|INVALID|CRITICAL/.test(s) ? C.bad : C.soft;

/* ═════════════════════════ GIANT WALL ═════════════════════════════ */
export function capital(ctx, W, H, hq) {
  const u = hq.util, q = hq.equity(), p = q.paper, sm = q.small, s = H / 1024;
  bg(ctx, W, H, '#5b8cff', {top: '#081426', bottom: '#03070e'});
  // the full BettorToken lockup (brand/) heads the capital wall; until it has
  // loaded the company name is set in type (the next repaint draws the logo)
  let bw;
  if (BRAND_LOGO.complete && BRAND_LOGO.naturalWidth) {
    const lh = 78 * s; bw = BRAND_LOGO.naturalWidth * lh / BRAND_LOGO.naturalHeight; ctx.drawImage(BRAND_LOGO, 70 * s, 50 * s, bw, lh);
  } else { text(ctx, 'BETTORTOKEN', 70 * s, 110 * s, C.ink, '800', 64 * s); font(ctx, '800', 64 * s); bw = ctx.measureText('BETTORTOKEN').width; }
  text(ctx, 'CAPITAL', 70 * s + bw + 26 * s, 110 * s, '#7f9dff', '300', 64 * s);
  chip(ctx, 'SEPARATE BOOKS · NEVER SUMMED', W - 70 * s, 104 * s, C.gold, 22 * s, 'right');
  const colW = (W - 200 * s) / 2, x1 = 70 * s, x2 = 70 * s + colW + 60 * s, top = 190 * s;
  ctx.fillStyle = C.line; ctx.fillRect(x2 - 30 * s, top, 2 * s, 640 * s);
  // PAPER
  text(ctx, 'PAPER EQUITY', x1, top + 34 * s, C.soft, '700', 26 * s, true);
  text(ctx, (p && p.label || '$500,000 PAPER EXPERIMENT') + ' · SIMULATED USD', x1, top + 72 * s, C.dim, '500', 21 * s, true, colW);
  if (p && typeof p.equity_usd === 'number') {
    text(ctx, u.usd(p.equity_usd), x1, top + 210 * s, C.ink, '300', 132 * s, false, colW);
  } else text(ctx, p ? p.status : 'UNAVAILABLE', x1, top + 200 * s, C.warn, '700', 90 * s, true, colW);
  const ps = p ? p.status : (q.status === 'LOADING' ? 'READING' : 'UNAVAILABLE');
  let cx = x1 + chip(ctx, ps, x1, top + 278 * s, stColor(ps), 24 * s) + 16 * s;
  if (p && p.no_new_mark) chip(ctx, 'NO NEW MARK', cx, top + 278 * s, C.warn, 24 * s);
  font(ctx, '500', 22 * s); ctx.fillStyle = C.soft;
  wrap(ctx, p ? (p.why || ('read ' + u.clock(p.source_at) + ' · last genuine mark update ' + u.clock(p.last_genuine_mark_update_at))) : (q.why || 'equity not read yet'), x1, top + 330 * s, colW, 30 * s, 2);
  const kv = (k, v, x, y, col) => { text(ctx, k, x, y, C.dim, '600', 20 * s, true); text(ctx, v, x, y + 46 * s, col || C.ink, '500', 40 * s, true, colW / 2 - 20 * s); };
  const dc = p && p.day_change;
  kv('DAY CHANGE', dc && dc.usd != null ? u.signedUsd(dc.usd) + (dc.pct != null ? ' (' + (dc.pct > 0 ? '+' : '') + dc.pct.toFixed(2) + '%)' : '') : 'UNAVAILABLE', x1, top + 430 * s, dc && dc.usd != null ? (dc.usd >= 0 ? C.good : C.bad) : C.warn);
  kv('REALIZED P&L', p && p.realized_pnl_usd != null ? u.signedUsd(p.realized_pnl_usd) : 'UNAVAILABLE', x1 + colW / 2, top + 430 * s, p && p.realized_pnl_usd != null ? (p.realized_pnl_usd >= 0 ? C.good : C.bad) : C.warn);
  kv('UNREALIZED (MARKED ONLY)', p && p.unrealized_pnl_usd != null ? u.signedUsd(p.unrealized_pnl_usd) : 'UNAVAILABLE', x1, top + 540 * s, p && p.unrealized_pnl_usd != null ? (p.unrealized_pnl_usd >= 0 ? C.good : C.bad) : C.warn);
  const op = p && p.open_positions;
  kv('OPEN POSITIONS', op && op.count != null ? op.count + '  ·  ' + op.marked + ' marked · ' + op.unmarked + ' unmarked' : 'UNAVAILABLE', x1 + colW / 2, top + 540 * s, op && op.count != null ? C.ink : C.warn);
  text(ctx, p && p.equity_treatment ? p.equity_treatment.text : 'equity treatment not read', x1, top + 640 * s, C.dim, '500', 19 * s, true, colW);
  // SMALL LIVE
  const st = sm && sm.status ? sm.status : (q.status === 'LOADING' ? 'READING' : 'UNAVAILABLE');
  text(ctx, 'SMALL LIVE · BETTOR ORIGINATED', x2, top + 34 * s, C.soft, '700', 26 * s, true, colW);
  text(ctx, 'real-money lane · its own state, never added to paper', x2, top + 72 * s, C.dim, '500', 21 * s, true, colW);
  text(ctx, u.words(st), x2, top + 200 * s, stColor(st), '700', 104 * s, false, colW);
  font(ctx, '500', 22 * s); ctx.fillStyle = C.soft;
  wrap(ctx, sm ? (sm.why || '') : (q.why || 'not read yet'), x2, top + 260 * s, colW, 30 * s, 3);
  kv('EQUITY', sm && sm.equity && sm.equity.usd != null ? u.usd(sm.equity.usd) : 'UNAVAILABLE', x2, top + 430 * s, sm && sm.equity && sm.equity.usd != null ? C.ink : C.warn);
  kv('ORDERS · FILLS', sm && sm.orders ? (sm.orders.submitted + ' submitted · ' + (sm.fills ? sm.fills.count : 0) + ' fills') : 'UNAVAILABLE', x2 + colW / 2, top + 430 * s, sm && sm.orders ? C.ink : C.warn);
  const ch = sm && sm.shadow_chain;
  kv('EDDIE SHADOW DECISIONS', ch && ch.eddie_estimates != null ? String(ch.eddie_estimates) : 'UNAVAILABLE', x2, top + 540 * s, ch && ch.eddie_estimates != null ? C.ink : C.warn);
  kv('LAST SHADOW DECISION', ch && ch.last_eddie_decision_at ? u.clock(ch.last_eddie_decision_at) : 'NONE RECORDED', x2 + colW / 2, top + 540 * s, C.ink);
  text(ctx, sm && sm.equity && sm.equity.usd == null ? 'equity: ' + (sm.equity.why || 'UNAVAILABLE') : '', x2, top + 640 * s, C.dim, '500', 19 * s, true, colW);
  // LEGACY MIRROR, per venue
  const by = H - 120 * s;
  ctx.fillStyle = 'rgba(255,255,255,.035)'; ctx.fillRect(0, by - 50 * s, W, 170 * s);
  text(ctx, 'LEGACY MIRROR VALIDATION · REAL MONEY · 1:1,000 · PER VENUE', x1, by - 10 * s, C.dim, '700', 20 * s, true);
  const ven = (v, name, x) => {
    const vs = v ? v.status : 'UNAVAILABLE';
    const w = chip(ctx, name + ' · ' + vs, x, by + 44 * s, stColor(vs), 20 * s);
    text(ctx, v && v.equity_usd != null ? u.usd(v.equity_usd) + (v.source_at ? ' · ' + u.clock(v.source_at) : '') : (v && v.why ? v.why : 'not read'), x + w + 18 * s, by + 44 * s, C.soft, '500', 20 * s, true, colW - w - 30 * s);
  };
  ven(q.pm, 'POLYMARKET US', x1); ven(q.kalshi, 'KALSHI', x2);
  text(ctx, 'source GET /api/command/equity/live' + (q.readAt ? ' · read ' + u.clock(q.readAt) : '') + (q.fixture ? ' · FIXTURE' : ''), W - 70 * s, H - 24 * s, C.dim, '500', 18 * s, true, W, 'right');
}

export function markets(ctx, W, H, hq) {
  const u = hq.util, q = hq.equity(), p = q.paper, rows = hq.tickers(), s = H / 1024;
  bg(ctx, W, H, '#4fe0a8');
  text(ctx, 'MARKETS', 60 * s, 96 * s, C.ink, '800', 54 * s);
  text(ctx, 'PAPER POSITIONS · REAL MARKS', 60 * s, 140 * s, C.soft, '600', 24 * s, true);
  if (p && p.no_new_mark) chip(ctx, 'NO NEW MARK', W - 60 * s, 96 * s, C.warn, 22 * s, 'right');
  else if (p) chip(ctx, p.status, W - 60 * s, 96 * s, stColor(p.status), 22 * s, 'right');
  if (!p) { na(ctx, W, H, q.why || 'equity not read yet'); return; }
  if (!rows.length) { text(ctx, 'No open paper position.', 60 * s, 300 * s, C.soft, '500', 30 * s); return; }
  const cols = [60, 760, 900, 1150, 1450, 1700].map((x) => x * W / 2048);
  const hy = 210 * s;
  ['MARKET', 'SIDE', 'MARK', 'STATE', 'OBSERVED', 'UNREALIZED'].forEach((h, i) => text(ctx, h, cols[i], hy, C.dim, '700', 19 * s, true));
  const rh = Math.min(92 * s, (H - 330 * s) / Math.min(rows.length, 8));
  rows.slice(0, 8).forEach((r, i) => {
    const y = hy + 60 * s + i * rh;
    if (i % 2 === 0) { ctx.fillStyle = 'rgba(255,255,255,.025)'; ctx.fillRect(40 * s, y - rh * 0.62, W - 80 * s, rh); }
    text(ctx, short(r.market), cols[0], y, C.ink, '600', 30 * s, false, cols[1] - cols[0] - 20 * s);
    text(ctx, r.side, cols[1], y, C.soft, '600', 26 * s, true);
    text(ctx, r.price != null ? r.price.toFixed(4) : '—', cols[2], y, r.price != null ? C.ink : C.dim, '500', 32 * s, true);
    text(ctx, u.words(r.state), cols[3], y, stColor(r.state), '700', 20 * s, true, cols[4] - cols[3] - 20 * s);
    text(ctx, r.price != null ? u.clock(r.at) : (r.why || 'no mark'), cols[4], y, C.soft, '500', 22 * s, true, cols[5] - cols[4] - 20 * s);
    text(ctx, r.pnl != null ? u.signedUsd(r.pnl) : 'UNMARKED', cols[5], y, r.pnl != null ? (r.pnl >= 0 ? C.good : C.bad) : C.warn, '500', 26 * s, true);
  });
  const ma = p.marks_as_of || {};
  text(ctx, 'source paper_book_observations · newest mark ' + u.clock(ma.newest_at) + ' · last genuine price change ' + u.clock(p.last_genuine_mark_update_at) + (q.fixture ? ' · FIXTURE' : ''), 60 * s, H - 36 * s, C.dim, '500', 19 * s, true, W - 120 * s);
}

export function decisions(ctx, W, H, hq) {
  const u = hq.util, rows = hq.decisions(), s = H / 1024, f = hq.reads.floor;
  bg(ctx, W, H, '#ff7d8e');
  text(ctx, 'DECISIONS', 60 * s, 96 * s, C.ink, '800', 54 * s);
  text(ctx, 'DEREK · ENTRIES AND REFUSALS · PAPER', 60 * s, 140 * s, C.soft, '600', 24 * s, true);
  if (!f.data) { na(ctx, W, H, f.why || 'the floor has not been read yet'); return; }
  if (!rows.length) { text(ctx, 'No paper decision recorded.', 60 * s, 300 * s, C.soft, '500', 30 * s); return; }
  const n = Math.min(rows.length, 8), rh = Math.min(96 * s, (H - 300 * s) / n);
  rows.slice(0, n).forEach((d, i) => {
    const y = 230 * s + i * rh;
    if (i % 2 === 0) { ctx.fillStyle = 'rgba(255,255,255,.025)'; ctx.fillRect(40 * s, y - rh * 0.62, W - 80 * s, rh); }
    text(ctx, u.clock(d.at).replace(' UTC', ''), 60 * s, y, C.dim, '500', 24 * s, true);
    chip(ctx, d.verdict || '—', 250 * s, y, d.verdict === 'ENTER' ? C.good : C.bad, 20 * s);
    text(ctx, short(d.market || d.id) + (d.side ? ' · ' + d.side : ''), 480 * s, y, C.ink, '600', 30 * s, false, 760 * s);
    text(ctx, d.refusal ? u.words(d.refusal) : (d.limit_price != null ? 'limit ' + d.limit_price.toFixed(2) : ''), 1280 * s, y, d.refusal ? C.warn : C.soft, '600', 22 * s, true, 470 * s);
    text(ctx, d.p_blended != null ? 'p ' + d.p_blended.toFixed(3) : '', W - 60 * s, y, C.soft, '500', 22 * s, true, 200 * s, 'right');
  });
  text(ctx, 'source paper_decisions via GET /api/command/floor · read ' + u.clock(f.lastOk) + (hq.fixture() ? ' · FIXTURE' : ''), 60 * s, H - 36 * s, C.dim, '500', 19 * s, true, W - 120 * s);
}

export function coverageWall(ctx, W, H, hq) {
  const u = hq.util, cv = hq.coverage(), s = H / 640;
  bg(ctx, W, H, '#f3ae68');
  text(ctx, 'COVERAGE', 40 * s, 66 * s, C.ink, '800', 40 * s);
  text(ctx, 'LEAGUE STATUS · TODAY', 40 * s, 100 * s, C.soft, '600', 17 * s, true);
  if (cv.status !== 'OK') { na(ctx, W, H, (cv.status === 'LOADING' ? 'reading /api/command/coverage' : cv.status) + (cv.why ? ' · ' + cv.why : '')); return; }
  const rows = cv.rows.slice(0, 12), cw = (W - 80 * s) / 3;
  rows.forEach((r, i) => {
    const x = 40 * s + (i % 3) * cw, y = 160 * s + Math.floor(i / 3) * 92 * s;
    ctx.fillStyle = 'rgba(255,255,255,.03)'; rr(ctx, x, y - 40 * s, cw - 16 * s, 76 * s, 10 * s); ctx.fill();
    text(ctx, r.league_name || r.league, x + 18 * s, y, C.ink, '700', 24 * s, false, cw - 50 * s);
    text(ctx, u.words(r.status), x + 18 * s, y + 26 * s, stColor(r.status), '700', 14 * s, true, cw - 50 * s);
  });
  text(ctx, Object.keys(cv.counts).map((k) => u.words(k) + ' ' + cv.counts[k]).join(' · '), 40 * s, H - 60 * s, C.soft, '600', 15 * s, true, W - 80 * s);
  text(ctx, 'source GET /api/command/coverage · day ' + (cv.day || '—') + ' ' + (cv.tz || '') + ' · read ' + u.clock(cv.readAt), 40 * s, H - 24 * s, C.dim, '500', 14 * s, true, W - 80 * s);
}

export function xavierWall(ctx, W, H, hq) {
  const u = hq.util, xd = hq.xavierDecisions(), d = hq.desk('xavier'), s = H / 640;
  bg(ctx, W, H, '#86c8f4');
  text(ctx, 'PORTFOLIO', 40 * s, 66 * s, C.ink, '800', 40 * s);
  text(ctx, 'XAVIER · CURRENT MANAGEMENT DECISIONS', 40 * s, 100 * s, C.soft, '600', 17 * s, true);
  chip(ctx, d.label, W - 40 * s, 66 * s, d.color, 17 * s, 'right');
  if (xd.status !== 'OK') { na(ctx, W, H, (xd.status === 'LOADING' ? 'reading /api/command/floor/xavier' : xd.status) + (xd.why ? ' · ' + xd.why : '')); return; }
  if (!xd.rows.length) { text(ctx, 'No management assessment recorded.', 40 * s, 200 * s, C.soft, '500', 22 * s); return; }
  xd.rows.slice(0, 5).forEach((r, i) => {
    const y = 160 * s + i * 86 * s, stt = r.recommendation_state || r.management_state || r.verdict || 'UNKNOWN';
    ctx.fillStyle = 'rgba(255,255,255,.03)'; rr(ctx, 30 * s, y - 34 * s, W - 60 * s, 74 * s, 10 * s); ctx.fill();
    chip(ctx, u.words(stt), 46 * s, y, stColor(stt === 'CURRENT' ? 'OK' : stt), 14 * s);
    text(ctx, u.clock(r.at), W - 46 * s, y, C.dim, '500', 14 * s, true, 200 * s, 'right');
    text(ctx, (r.summary || '').replace(/^[A-Z_]+( \([^)]*\))? · /, ''), 46 * s, y + 28 * s, C.ink, '500', 16 * s, false, W - 340 * s);
    text(ctx, r.age_seconds != null ? 'evidence age ' + Math.round(r.age_seconds) + 's / limit ' + (r.freshness_limit != null ? Math.round(r.freshness_limit) + 's' : '—') : '', W - 46 * s, y + 28 * s, r.age_seconds != null && r.freshness_limit != null && r.age_seconds > r.freshness_limit ? C.warn : C.soft, '500', 14 * s, true, 300 * s, 'right');
  });
  text(ctx, 'source xavier_management_assessments via GET /api/command/floor/xavier · read ' + u.clock(xd.readAt), 40 * s, H - 24 * s, C.dim, '500', 14 * s, true, W - 80 * s);
}

/* ═════════════════════════ ZONE BOARDS ════════════════════════════ */
export function zoneBoard(ctx, W, H, hq, slug) {
  const u = hq.util, d = hq.desk(slug), s = H / 864;
  bg(ctx, W, H, d.accent, {top: '#0b1523', bottom: '#04080f'});
  text(ctx, d.zone.toUpperCase(), 60 * s, 92 * s, d.accent, '700', 30 * s, true, W - 120 * s);
  text(ctx, d.name, 60 * s, 210 * s, C.ink, '300', 120 * s, false, W * 0.55);
  text(ctx, d.role, 64 * s, 268 * s, C.soft, '500', 36 * s, false, W * 0.55);
  let cx = W - 60 * s;
  cx -= chip(ctx, d.label, cx, 120 * s, d.color, 30 * s, 'right') + 14 * s;
  if (d.shadow) chip(ctx, 'SHADOW', cx, 120 * s, C.gold, 30 * s, 'right');
  font(ctx, '400', 34 * s); ctx.fillStyle = '#cfdbe7';
  wrap(ctx, d.detail, 60 * s, 360 * s, W - 120 * s, 46 * s, 2);
  ctx.fillStyle = C.line; ctx.fillRect(60 * s, 470 * s, W - 120 * s, 2 * s);
  const mons = d.monitor.slice(0, 3);
  if (mons.length) {
    const cw = (W - 120 * s) / mons.length;
    mons.forEach((m, i) => {
      const x = 60 * s + i * cw;
      text(ctx, m.label.toUpperCase(), x, 530 * s, C.dim, '700', 20 * s, true, cw - 30 * s);
      text(ctx, m.value == null ? 'UNAVAILABLE' : String(m.value), x, 610 * s, m.value == null ? C.warn : C.ink, '500', (m.value == null ? 36 : String(m.value).length > 14 ? 30 : 64) * s, false, cw - 30 * s);
      text(ctx, m.value == null ? (m.why || '') : (m.as_of ? u.clock(m.as_of) : m.source), x, 650 * s, C.dim, '500', 18 * s, true, cw - 30 * s);
    });
  } else text(ctx, d.deployed === false ? 'Not deployed: ' + (d.deployWhy || 'NO_TABLES') : 'No monitor metric in this read.', 60 * s, 600 * s, C.soft, '500', 30 * s, false, W - 120 * s);
  if (d.last && d.last.summary) {
    text(ctx, 'LATEST OUTPUT', 60 * s, 730 * s, C.dim, '700', 20 * s, true);
    text(ctx, d.last.summary, 60 * s, 772 * s, C.ink, '500', 28 * s, false, W - 380 * s);
    text(ctx, d.last.at ? u.clock(d.last.at) : '', W - 60 * s, 772 * s, C.soft, '500', 22 * s, true, 300 * s, 'right');
  }
  text(ctx, (d.heartbeatAt ? 'heartbeat ' + u.ago(d.heartbeatAt) : d.deployed === false ? 'not deployed' : 'no heartbeat recorded') + ' · GET /api/command/floor' + (d.readStale ? ' · STALE READ' : '') + (hq.fixture() ? ' · FIXTURE' : ''), 60 * s, H - 26 * s, C.dim, '500', 18 * s, true, W - 120 * s);
}
export function zoneSign(ctx, W, H, hq, slug) {
  const d = hq.desk(slug), s = H / 160;
  ctx.clearRect(0, 0, W, H);
  text(ctx, d.name.toUpperCase(), 0, 92 * s, '#f4f8fc', '700', 84 * s);
  font(ctx, '700', 84 * s); const nw = ctx.measureText(d.name.toUpperCase()).width;
  ctx.fillStyle = d.accent; ctx.fillRect(nw + 34 * s, 40 * s, 4 * s, 64 * s);
  text(ctx, d.zone.toUpperCase(), nw + 68 * s, 90 * s, '#b7c6d6', '500', 40 * s, false, W - nw - 80 * s);
  ctx.fillStyle = d.color; ctx.beginPath(); ctx.arc(14 * s, 136 * s, 8 * s, 0, Math.PI * 2); ctx.fill();
  text(ctx, d.label + (d.shadow ? ' · SHADOW' : ''), 34 * s, 146 * s, d.color, '700', 30 * s, true);
}
export function arriving(ctx, W, H, hq, slug) {
  const d = hq.desk(slug), s = W / 512;
  ctx.clearRect(0, 0, W, H);
  const g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, hexA(d.accent, 0.22)); g.addColorStop(1, 'rgba(6,12,22,.55)');
  ctx.fillStyle = g; rr(ctx, 6 * s, 6 * s, W - 12 * s, H - 12 * s, 26 * s); ctx.fill();
  ctx.strokeStyle = hexA(d.accent, 0.8); ctx.lineWidth = 3 * s; rr(ctx, 6 * s, 6 * s, W - 12 * s, H - 12 * s, 26 * s); ctx.stroke();
  text(ctx, d.name, W / 2, H * 0.36, '#f4f8fc', '300', 92 * s, false, W - 60 * s, 'center');
  text(ctx, d.role.toUpperCase(), W / 2, H * 0.46, '#c6d3e0', '600', 24 * s, true, W - 60 * s, 'center');
  ctx.fillStyle = hexA(d.accent, 0.6); ctx.fillRect(W * 0.3, H * 0.53, W * 0.4, 2 * s);
  text(ctx, 'PORTRAIT', W / 2, H * 0.66, d.accent, '700', 44 * s, true, W - 60 * s, 'center');
  text(ctx, 'ARRIVING', W / 2, H * 0.75, d.accent, '700', 44 * s, true, W - 60 * s, 'center');
  text(ctx, d.label, W / 2, H * 0.88, d.color, '700', 26 * s, true, W - 60 * s, 'center');
}
export function nameplate(ctx, W, H, hq, slug) {
  const d = hq.desk(slug), s = H / 96;
  ctx.fillStyle = '#05090f'; ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = d.accent; ctx.fillRect(0, H - 4 * s, W, 4 * s);
  text(ctx, d.name.toUpperCase(), 24 * s, 54 * s, '#f1f6fb', '700', 38 * s);
  font(ctx, '700', 38 * s); const nw = ctx.measureText(d.name.toUpperCase()).width;
  text(ctx, d.role.toUpperCase(), 44 * s + nw, 52 * s, '#93a6ba', '600', 18 * s, true, W - nw - 70 * s);
  text(ctx, d.label, 24 * s, 82 * s, d.color, '700', 16 * s, true);
}

/* ═════════════════════════ DESK MONITORS ══════════════════════════ */
export const DESK_SCREENS = {
  derek: ['feed', 'status', 'refusals'],
  karen: ['challenges', 'status', 'edges'],
  scout: ['coverage', 'status', 'metrics'],
  allocator: ['ranking', 'status', 'metrics'],
  eddie: ['marks', 'smalllive', 'status', 'positions', 'metrics', 'edges'],
  audrey: ['audit', 'composition', 'status', 'edges'],
  xavier: ['xavier', 'positions', 'status']
};
export function monitor(ctx, W, H, hq, slug, kind) {
  const u = hq.util, d = hq.desk(slug), s = H / 320;
  if (d.code === 'NOT_DEPLOYED' || d.code === 'UNAVAILABLE') {
    ctx.fillStyle = '#020407'; ctx.fillRect(0, 0, W, H);
    text(ctx, d.code === 'NOT_DEPLOYED' ? 'OFFLINE' : 'NO READ', W / 2, H / 2 - 6 * s, '#3c4757', '700', 26 * s, true, W - 40 * s, 'center');
    text(ctx, d.code === 'NOT_DEPLOYED' ? 'not deployed · ' + (d.deployWhy || 'NO_TABLES') : (d.readWhy || 'the floor has not been read'), W / 2, H / 2 + 24 * s, '#334050', '500', 12 * s, true, W - 40 * s, 'center');
    return;
  }
  bg(ctx, W, H, d.accent);
  const P = PANELS[kind] || PANELS.status;
  P(ctx, W, H, hq, d, u, s);
  if (d.code === 'STALE' || d.readStale) {
    ctx.fillStyle = 'rgba(4,6,10,.55)'; ctx.fillRect(0, 0, W, H);
    chip(ctx, d.readStale ? 'STALE READ' : 'STALE', W / 2 - 50 * s, H / 2, C.warn, 16 * s);
  }
}
const rowsOf = (ctx, list, s, W, draw) => list.forEach((x, i) => draw(x, 76 * s + i * 38 * s, i));
const PANELS = {
  status(ctx, W, H, hq, d, u, s) {
    header(ctx, W, H, d.name + ' · desk', d.label, d.color);
    font(ctx, '400', 16 * s); ctx.fillStyle = '#cfdbe7';
    wrap(ctx, d.detail, 18 * s, 84 * s, W - 36 * s, 22 * s, 4);
    text(ctx, 'STATE SINCE', 18 * s, 200 * s, C.dim, '700', 10 * s, true);
    text(ctx, d.since ? u.clock(d.since) : '—', 18 * s, 224 * s, C.ink, '500', 18 * s, true);
    text(ctx, 'HEARTBEAT', W / 2, 200 * s, C.dim, '700', 10 * s, true);
    text(ctx, d.heartbeatAt ? u.ago(d.heartbeatAt) : (d.deployed === false ? 'not deployed' : 'none'), W / 2, 224 * s, C.ink, '500', 18 * s, true, W / 2 - 20 * s);
    if (d.shadow) chip(ctx, 'SHADOW · NO ORDER AUTHORITY', 18 * s, 266 * s, C.gold, 11 * s);
    footer(ctx, W, H, 'GET /api/command/floor · ' + u.clock(d.readAt));
  },
  metrics(ctx, W, H, hq, d, u, s) {
    header(ctx, W, H, 'Monitor', d.zone, d.accent);
    const ms = d.monitor.slice(0, 4);
    if (!ms.length) { na(ctx, W, H, d.deployed === false ? 'not deployed' : 'no monitor metric in this read'); return; }
    ms.forEach((m, i) => {
      const y = 82 * s + i * 52 * s;
      text(ctx, m.label.toUpperCase(), 18 * s, y, C.dim, '700', 10 * s, true, W * 0.6);
      text(ctx, m.value == null ? 'UNAVAILABLE' : String(m.value), 18 * s, y + 26 * s, m.value == null ? C.warn : C.ink, '500', (String(m.value).length > 22 ? 14 : 22) * s, false, W - 150 * s);
      text(ctx, m.value == null ? (m.why || '') : (m.as_of ? u.clock(m.as_of) : ''), W - 18 * s, y + 24 * s, C.dim, '500', 10 * s, true, 160 * s, 'right');
    });
    footer(ctx, W, H, 'sources: ' + ms.map((m) => m.source).filter(Boolean).slice(0, 2).join(' · '));
  },
  feed(ctx, W, H, hq, d, u, s) {
    header(ctx, W, H, 'Recorded decisions', 'PAPER', d.accent);
    const f = hq.decisions().slice(0, 6);
    if (!f.length) { na(ctx, W, H, hq.reads.floor.data ? 'no paper decision recorded' : hq.reads.floor.why); return; }
    rowsOf(ctx, f, s, W, (x, y) => {
      chip(ctx, x.verdict, 18 * s, y + 4 * s, x.verdict === 'ENTER' ? C.good : C.bad, 10 * s);
      text(ctx, short(x.market), 100 * s, y + 4 * s, C.ink, '600', 14 * s, false, W - 220 * s);
      text(ctx, u.clock(x.at).replace(' UTC', ''), W - 18 * s, y + 4 * s, C.dim, '500', 11 * s, true, 100 * s, 'right');
      if (x.refusal) text(ctx, u.words(x.refusal), 100 * s, y + 20 * s, C.warn, '600', 9 * s, true, W - 220 * s);
    });
    footer(ctx, W, H, 'paper_decisions · ' + u.clock(hq.reads.floor.lastOk));
  },
  refusals(ctx, W, H, hq, d, u, s) {
    header(ctx, W, H, 'Refusal reasons', 'LAST ' + hq.decisions().length + ' RECORDED', d.accent);
    const f = hq.decisions(), cnt = {};
    f.forEach((x) => { const k = x.verdict === 'ENTER' ? 'ENTER' : (x.refusal || x.verdict || 'UNKNOWN'); cnt[k] = (cnt[k] || 0) + 1; });
    const keys = Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a]).slice(0, 5);
    if (!keys.length) { na(ctx, W, H, 'no paper decision recorded'); return; }
    const max = Math.max.apply(null, keys.map((k) => cnt[k]));
    keys.forEach((k, i) => {
      const y = 86 * s + i * 40 * s;
      text(ctx, u.words(k), 18 * s, y, k === 'ENTER' ? C.good : C.ink, '600', 12 * s, true, W * 0.55);
      ctx.fillStyle = hexA(k === 'ENTER' ? C.good : d.accent, 0.75); ctx.fillRect(18 * s, y + 8 * s, (W - 100 * s) * cnt[k] / max, 8 * s);
      text(ctx, String(cnt[k]), W - 18 * s, y + 16 * s, C.ink, '600', 14 * s, true, 60 * s, 'right');
    });
    footer(ctx, W, H, 'counted from the decisions in this read');
  },
  ranking(ctx, W, H, hq, d, u, s) {
    header(ctx, W, H, 'Shadow sleeve ranking', 'SHADOW', C.gold);
    const r = hq.ranking().slice(0, 6);
    if (!r.length) { na(ctx, W, H, (hq.reads.floor.data && hq.reads.floor.data.sections && (hq.reads.floor.data.sections['opportunities.intel_allocations'] || {}).why) || 'no allocator run recorded'); return; }
    const max = Math.max.apply(null, r.map((x) => x.shadow_usd || 0)) || 1;
    rowsOf(ctx, r, s, W, (x, y) => {
      text(ctx, String(x.rank), 18 * s, y + 6 * s, C.dim, '700', 14 * s, true);
      text(ctx, short(x.market || x.id), 44 * s, y + 6 * s, C.ink, '600', 13 * s, false, W * 0.48);
      ctx.fillStyle = hexA(C.gold, 0.7); ctx.fillRect(44 * s, y + 14 * s, (W * 0.48) * (x.shadow_usd || 0) / max, 4 * s);
      text(ctx, x.shadow_usd != null ? u.usd(x.shadow_usd) : '—', W - 18 * s, y + 6 * s, C.ink, '500', 13 * s, true, 110 * s, 'right');
      text(ctx, x.binding_constraint ? u.words(x.binding_constraint) : '', W - 130 * s, y + 6 * s, C.soft, '600', 9 * s, true, 110 * s, 'right');
    });
    footer(ctx, W, H, 'intel_allocations · ' + (r[0].at ? u.clock(r[0].at) : ''));
  },
  xavier(ctx, W, H, hq, d, u, s) {
    header(ctx, W, H, 'Management decisions', 'XAVIER', d.accent);
    const xd = hq.xavierDecisions();
    if (xd.status !== 'OK' || !xd.rows.length) { na(ctx, W, H, xd.status !== 'OK' ? (xd.why || xd.status) : 'no assessment recorded'); return; }
    rowsOf(ctx, xd.rows.slice(0, 5), s, W, (x, y) => {
      const st = x.recommendation_state || x.verdict || '';
      chip(ctx, u.words(st), 18 * s, y + 6 * s, stColor(st === 'CURRENT' ? 'OK' : st), 9 * s);
      text(ctx, u.clock(x.at).replace(' UTC', ''), W - 18 * s, y + 6 * s, C.dim, '500', 10 * s, true, 90 * s, 'right');
      text(ctx, (x.summary || '').replace(/^[A-Z_]+( \([^)]*\))? · /, ''), 18 * s, y + 24 * s, C.soft, '500', 10 * s, true, W - 36 * s);
    });
    footer(ctx, W, H, 'xavier_management_assessments · ' + u.clock(xd.readAt));
  },
  positions(ctx, W, H, hq, d, u, s) {
    header(ctx, W, H, 'Paper positions', 'REAL MARKS', d.accent);
    const t = hq.tickers().slice(0, 6), p = hq.equity().paper;
    if (!p) { na(ctx, W, H, hq.equity().why || 'equity not read'); return; }
    if (!t.length) { na(ctx, W, H, 'no open paper position'); return; }
    rowsOf(ctx, t, s, W, (x, y) => {
      text(ctx, short(x.market) + ' ' + x.side, 18 * s, y + 6 * s, C.ink, '600', 12 * s, false, W * 0.5);
      text(ctx, x.price != null ? x.price.toFixed(4) : 'UNMARKED', W * 0.58, y + 6 * s, x.price != null ? C.ink : C.warn, '500', 13 * s, true);
      text(ctx, x.pnl != null ? u.signedUsd(x.pnl) : '—', W - 18 * s, y + 6 * s, x.pnl == null ? C.dim : x.pnl >= 0 ? C.good : C.bad, '500', 12 * s, true, 100 * s, 'right');
      text(ctx, x.price != null ? u.words(x.state) + ' · ' + u.clock(x.at) : (x.why || ''), 18 * s, y + 22 * s, C.dim, '500', 9 * s, true, W - 36 * s);
    });
    footer(ctx, W, H, 'paper_book_observations · equity/live');
  },
  marks(ctx, W, H, hq, d, u, s) {
    header(ctx, W, H, 'Mark microstructure', 'PAPER BOOK', d.accent);
    const t = hq.tickers(), p = hq.equity().paper, nw = hq.util.epoch(Date.now() / 1000);
    if (!p) { na(ctx, W, H, hq.equity().why || 'equity not read'); return; }
    const ma = p.marks_as_of || {};
    text(ctx, 'MARK METHOD', 18 * s, 78 * s, C.dim, '700', 10 * s, true);
    text(ctx, ma.method || 'UNAVAILABLE', 18 * s, 98 * s, C.ink, '500', 14 * s, true, W / 2 - 30 * s);
    text(ctx, 'STALE AFTER', W / 2, 78 * s, C.dim, '700', 10 * s, true);
    text(ctx, ma.stale_mark_after_s != null ? Math.round(ma.stale_mark_after_s) + 's' : '—', W / 2, 98 * s, C.ink, '500', 14 * s, true);
    // one bar per position: the AGE of its real mark against the stale bound
    const lim = ma.stale_mark_after_s || 300;
    t.slice(0, 6).forEach((x, i) => {
      const y = 128 * s + i * 24 * s, at = u.epoch(x.at), age = at != null ? Math.max(0, nw - at) : null;
      text(ctx, short(x.market), 18 * s, y + 9 * s, C.soft, '600', 10 * s, true, W * 0.36);
      ctx.fillStyle = 'rgba(255,255,255,.06)'; ctx.fillRect(W * 0.4, y, W * 0.42, 10 * s);
      if (age != null) { ctx.fillStyle = age > lim ? C.warn : d.accent; ctx.fillRect(W * 0.4, y, W * 0.42 * Math.min(1, age / lim), 10 * s); }
      text(ctx, age != null ? Math.round(age) + 's' : 'UNMARKED', W - 18 * s, y + 9 * s, age == null ? C.warn : age > lim ? C.warn : C.ink, '500', 10 * s, true, 70 * s, 'right');
    });
    footer(ctx, W, H, 'mark age vs stale bound · spread/depth not in this read');
  },
  smalllive(ctx, W, H, hq, d, u, s) {
    const sm = hq.equity().small;
    header(ctx, W, H, 'Small Live · BETTOR originated', sm && sm.status ? u.words(sm.status) : 'UNAVAILABLE', sm ? stColor(sm.status || '') : C.warn);
    if (!sm) { na(ctx, W, H, hq.equity().why || 'not read'); return; }
    font(ctx, '400', 14 * s); ctx.fillStyle = '#cfdbe7'; wrap(ctx, sm.why || '', 18 * s, 82 * s, W - 36 * s, 19 * s, 3);
    const ch = sm.shadow_chain || {};
    [['SHADOW DECISIONS', ch.eddie_estimates != null ? String(ch.eddie_estimates) : 'UNAVAILABLE'], ['ORDERS SUBMITTED', sm.orders ? String(sm.orders.submitted) : 'UNAVAILABLE'],
     ['FILLS', sm.fills ? String(sm.fills.count) : 'UNAVAILABLE'], ['LAST SHADOW', ch.last_eddie_decision_at ? u.clock(ch.last_eddie_decision_at) : 'NONE']].forEach((kv, i) => {
      const x = 18 * s + (i % 2) * (W / 2), y = 168 * s + Math.floor(i / 2) * 54 * s;
      text(ctx, kv[0], x, y, C.dim, '700', 10 * s, true); text(ctx, kv[1], x, y + 24 * s, /UNAVAIL/.test(kv[1]) ? C.warn : C.ink, '500', 18 * s, true, W / 2 - 30 * s);
    });
    footer(ctx, W, H, 'small_live_bettor · equity/live');
  },
  audit(ctx, W, H, hq, d, u, s) {
    header(ctx, W, H, 'Audit ledger', 'RECONCILIATION', d.accent);
    const ms = d.monitor;
    ms.slice(0, 3).forEach((m, i) => {
      const y = 86 * s + i * 50 * s;
      text(ctx, m.label.toUpperCase(), 18 * s, y, C.dim, '700', 10 * s, true, W * 0.7);
      text(ctx, m.value == null ? 'UNAVAILABLE' : String(m.value), 18 * s, y + 26 * s, m.value == null ? C.warn : C.ink, '500', 22 * s, true);
    });
    if (d.last) { font(ctx, '500', 11 * s); ctx.fillStyle = C.soft; wrap(ctx, 'last: ' + d.last.summary, 18 * s, 248 * s, W - 36 * s, 15 * s, 2); }
    footer(ctx, W, H, 'paper_audrey_findings · coverage_collapse_alerts');
  },
  composition(ctx, W, H, hq, d, u, s) {
    const p = hq.equity().paper;
    header(ctx, W, H, 'Paper equity composition', p ? p.status : 'UNAVAILABLE', p ? stColor(p.status) : C.warn);
    if (!p) { na(ctx, W, H, hq.equity().why || 'not read'); return; }
    const ex = p.exposure || {};
    [['CASH', p.cash_usd], ['MARKED VALUE', ex.marked_value_usd], ['UNMARKED AT COST', ex.unmarked_cost_basis_usd], ['EQUITY (SERVER)', p.equity_usd]].forEach((kv, i) => {
      const y = 84 * s + i * 40 * s;
      text(ctx, kv[0], 18 * s, y, i === 3 ? C.ink : C.dim, '700', 11 * s, true);
      text(ctx, kv[1] != null ? u.usd(kv[1]) : 'UNAVAILABLE', W - 18 * s, y, kv[1] != null ? C.ink : C.warn, '500', 15 * s, true, W * 0.5, 'right');
      if (i === 2) { ctx.fillStyle = C.line; ctx.fillRect(18 * s, y + 12 * s, W - 36 * s, s); }
    });
    font(ctx, '500', 10 * s); ctx.fillStyle = C.dim; wrap(ctx, p.equity_treatment ? p.equity_treatment.text : '', 18 * s, 254 * s, W - 36 * s, 13 * s, 2);
    footer(ctx, W, H, 'paper book only · as reported by equity/live');
  },
  challenges(ctx, W, H, hq, d, u, s) {
    header(ctx, W, H, 'Red team challenges', 'ZERO AUTHORITY', d.accent);
    const ms = d.monitor;
    ms.slice(0, 2).forEach((m, i) => {
      text(ctx, m.label.toUpperCase(), 18 * s + i * (W / 2), 82 * s, C.dim, '700', 10 * s, true, W / 2 - 30 * s);
      text(ctx, m.value == null ? 'UNAVAILABLE' : String(m.value), 18 * s + i * (W / 2), 124 * s, m.value == null ? C.warn : C.ink, '300', (m.value == null ? 18 : 40) * s, false, W / 2 - 30 * s);
    });
    if (d.last) { text(ctx, 'LATEST', 18 * s, 168 * s, C.dim, '700', 10 * s, true); font(ctx, '500', 13 * s); ctx.fillStyle = C.ink; wrap(ctx, d.last.summary, 18 * s, 190 * s, W - 36 * s, 18 * s, 3); }
    footer(ctx, W, H, 'karen_challenges · ' + (d.last && d.last.at ? u.clock(d.last.at) : ''));
  },
  edges(ctx, W, H, hq, d, u, s) {
    header(ctx, W, H, 'Collaboration · last hour', 'REAL EDGES', d.accent);
    const e = hq.edges().filter((x) => x.from === d.slug || x.to === d.slug).slice(0, 6);
    if (!e.length) { text(ctx, 'No hand-off or challenge involving ' + d.name + ' in the window.', 18 * s, 96 * s, C.soft, '500', 12 * s, false, W - 36 * s); footer(ctx, W, H, 'edges · GET /api/command/floor'); return; }
    rowsOf(ctx, e, s, W, (x, y) => {
      const other = hq.BY_SLUG[x.from === d.slug ? x.to : x.from];
      text(ctx, (x.from === d.slug ? '→ ' : '← ') + (other ? other.name : '?'), 18 * s, y + 6 * s, other ? other.accent : C.soft, '700', 13 * s, false, 110 * s);
      text(ctx, x.label + (x.count > 1 ? ' ×' + x.count : ''), 130 * s, y + 6 * s, C.ink, '500', 12 * s, false, W - 240 * s);
      text(ctx, u.clock(x.at).replace(' UTC', ''), W - 18 * s, y + 6 * s, C.dim, '500', 10 * s, true, 90 * s, 'right');
    });
    footer(ctx, W, H, 'edges · GET /api/command/floor');
  },
  pipeline(ctx, W, H, hq, d, u, s) {
    header(ctx, W, H, 'Discovery pipeline', 'PAPER · 24H', d.accent);
    const ms = d.monitor.slice(0, 2);
    ms.forEach((m, i) => {
      const x = 18 * s + i * (W / 2);
      text(ctx, m.label.toUpperCase(), x, 76 * s, C.dim, '700', 10 * s, true, W / 2 - 30 * s);
      text(ctx, m.value == null ? 'UNAVAILABLE' : String(m.value), x, 116 * s, m.value == null ? C.warn : i ? C.good : C.ink, '300', (m.value == null ? 18 : 40) * s, false, W / 2 - 30 * s);
    });
    if (ms.length === 2 && typeof ms[0].value === 'number' && typeof ms[1].value === 'number' && ms[0].value > 0) {
      const f = ms[1].value / ms[0].value;
      ctx.fillStyle = 'rgba(255,255,255,.07)'; ctx.fillRect(18 * s, 134 * s, W - 36 * s, 8 * s);
      ctx.fillStyle = C.good; ctx.fillRect(18 * s, 134 * s, (W - 36 * s) * f, 8 * s);
      text(ctx, (f * 100).toFixed(1) + '% of recorded decisions were ENTER', 18 * s, 160 * s, C.soft, '500', 10 * s, true, W - 36 * s);
    }
    hq.decisions().slice(0, 3).forEach((x, i) => {
      const y = 190 * s + i * 30 * s;
      chip(ctx, x.verdict, 18 * s, y, x.verdict === 'ENTER' ? C.good : C.bad, 9 * s);
      text(ctx, short(x.market) + (x.refusal ? ' · ' + u.words(x.refusal) : ''), 96 * s, y, C.ink, '500', 11 * s, false, W - 190 * s);
      text(ctx, u.clock(x.at).replace(' UTC', ''), W - 18 * s, y, C.dim, '500', 10 * s, true, 80 * s, 'right');
    });
    footer(ctx, W, H, 'paper_decisions · GET /api/command/floor');
  },
  coverage(ctx, W, H, hq, d, u, s) {
    header(ctx, W, H, 'Coverage scan', 'LEAGUES', d.accent);
    const cv = hq.coverage();
    if (cv.status !== 'OK') { na(ctx, W, H, cv.status + (cv.why ? ' · ' + cv.why : '')); return; }
    cv.rows.slice(0, 9).forEach((r, i) => {
      const x = 18 * s + (i % 3) * ((W - 36 * s) / 3), y = 84 * s + Math.floor(i / 3) * 62 * s;
      text(ctx, r.league_name || r.league, x, y, C.ink, '700', 15 * s, false, (W - 60 * s) / 3);
      text(ctx, u.words(r.status), x, y + 20 * s, stColor(r.status), '700', 8 * s, true, (W - 70 * s) / 3);
    });
    footer(ctx, W, H, 'coverage · day ' + (cv.day || '—'));
  }
};

/* ═════════════════════════ TICKER + CORE ══════════════════════════ */
/* Returns the ticker strip's text items: real marks and real decisions,
 * each with its own timestamp. */
export function tickerItems(hq) {
  const u = hq.util, out = [], q = hq.equity();
  hq.tickers().forEach((t) => out.push({c: t.price != null ? C.ink : C.warn,
    s: short(t.market) + ' ' + t.side + '  ' + (t.price != null ? t.price.toFixed(4) + '  ' + u.words(t.state) + ' ' + u.clock(t.at) + (t.pnl != null ? '  uPnL ' + u.signedUsd(t.pnl) : '') : 'UNMARKED · ' + (t.why || 'no mark'))}));
  hq.decisions().slice(0, 6).forEach((d) => out.push({c: d.verdict === 'ENTER' ? C.good : '#ff9aa6',
    s: 'DEREK ' + d.verdict + ' ' + short(d.market || d.id) + (d.refusal ? ' · ' + u.words(d.refusal) : '') + '  ' + u.clock(d.at)}));
  if (q.paper) out.unshift({c: '#a9c4ff', s: 'PAPER EQUITY ' + (u.usd(q.paper.equity_usd) || 'UNAVAILABLE') + ' · ' + q.paper.status + (q.paper.no_new_mark ? ' · NO NEW MARK' : '') + '  ' + u.clock(q.paper.source_at)});
  if (q.small) out.unshift({c: C.gold, s: 'SMALL LIVE ' + u.words(q.small.status || 'UNAVAILABLE')});
  if (!out.length) out.push({c: C.warn, s: 'MARKET DATA UNAVAILABLE · ' + (q.why || hq.reads.floor.why || 'not read yet')});
  return out;
}
export function ticker(ctx, W, H, hq) {
  const items = tickerItems(hq), s = H / 128;
  ctx.fillStyle = '#03060b'; ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = 'rgba(120,170,255,.08)'; ctx.fillRect(0, 0, W, 3 * s); ctx.fillRect(0, H - 3 * s, W, 3 * s);
  font(ctx, '600', 54 * s, true);
  let x = 24 * s;
  // fill the strip with the real items (repeated if short, so it wraps cleanly)
  for (let guard = 0; x < W && guard < 64; guard++) {
    const it = items[guard % items.length];
    if (x + ctx.measureText(it.s).width > W - 24 * s) break;   // whole items only: no cut line at the wrap seam
    ctx.fillStyle = it.c; ctx.fillText(it.s, x, 84 * s);
    x += ctx.measureText(it.s).width + 40 * s;
    ctx.fillStyle = '#3b5f9a'; ctx.fillText('◆', x, 82 * s); x += 80 * s;
  }
}
export function coreBand(ctx, W, H, hq) {
  const u = hq.util, q = hq.equity(), p = q.paper, sm = q.small, s = H / 512;
  ctx.clearRect(0, 0, W, H);
  const block = (x0, w, kind) => {
    const g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, 'rgba(40,80,170,.30)'); g.addColorStop(1, 'rgba(10,20,40,.05)');
    ctx.fillStyle = g; ctx.fillRect(x0 + 10 * s, 20 * s, w - 20 * s, H - 40 * s);
    ctx.fillStyle = kind === 'paper' ? '#7fa2ff' : C.gold; ctx.fillRect(x0 + 10 * s, 20 * s, w - 20 * s, 4 * s);
    const cx = x0 + w / 2;
    if (kind === 'paper') {
      text(ctx, 'PAPER EQUITY · SIMULATED', cx, 110 * s, '#b8ccff', '700', 34 * s, true, w - 60 * s, 'center');
      text(ctx, p && p.equity_usd != null ? u.usd(p.equity_usd) : (p ? p.status : 'UNAVAILABLE'), cx, 260 * s, '#f2f6ff', '300', 112 * s, false, w - 60 * s, 'center');
      const st = p ? p.status + (p.no_new_mark ? ' · NO NEW MARK' : '') : (q.why || 'NOT READ');
      text(ctx, st, cx, 350 * s, p ? stColor(p.status) : C.warn, '700', 34 * s, true, w - 60 * s, 'center');
      text(ctx, p && p.source_at ? 'as of ' + u.clock(p.source_at) : '', cx, 420 * s, '#8ea3c4', '500', 30 * s, true, w - 60 * s, 'center');
    } else {
      text(ctx, 'SMALL LIVE · BETTOR ORIGINATED', cx, 110 * s, '#f1dca4', '700', 34 * s, true, w - 60 * s, 'center');
      text(ctx, sm && sm.status ? u.words(sm.status) : 'UNAVAILABLE', cx, 260 * s, stColor(sm && sm.status || 'UNAVAILABLE'), '600', 112 * s, false, w - 60 * s, 'center');
      text(ctx, sm && sm.equity ? (sm.equity.usd != null ? u.usd(sm.equity.usd) : 'equity UNAVAILABLE') : 'not read', cx, 350 * s, '#cbb98a', '700', 32 * s, true, w - 60 * s, 'center');
      text(ctx, 'separate book · never summed', cx, 420 * s, '#8ea3c4', '500', 30 * s, true, w - 60 * s, 'center');
    }
  };
  const w = W / 4;
  block(0, w, 'paper'); block(w, w, 'small'); block(2 * w, w, 'paper'); block(3 * w, w, 'small');
}
