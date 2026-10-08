// TRADER DEVICE ACCEPTANCE (read only), for frontend-preview.yml.
//
// The PM's acceptance for Trader Mode is not "the page renders": every open
// position, its offsetting standing orders, real venue quotes, Xavier's
// recorded action, game state only from an authorized source, logos, and the
// phone / tablet layout must be shown as the production API records them,
// with no simulated price and no fabricated operating state. This compares
// the rendered page with the API's own snapshot, position by position, on
// every device:
//
//   phase LIVE    the page polls /api/command/paper/trader-mode through the
//                 preview proxy (production data, GET only); every response
//                 is kept in memory (never written out) and the last one is
//                 the reference snapshot S
//   phase FROZEN  from then on the page's polls are answered with S itself,
//                 so the data cannot change: the DOM is read twice 10 s apart
//                 and any price, P/L, count or score that moved was produced
//                 by the page, not by the venue (a simulated quote fails here)
//
// Fidelity (DOM vs S): the card set equals the API's position_id set and the
// wall / order counts equal the API totals; each card's bid, target, P/L and
// order strip equal the snapshot values in the page's own formats; the orders
// view lists exactly the standing orders with their limits and states; the
// focus desk shows the recorded bid, the recorded recommendation (never one
// the API does not hold) or "Waiting for evidence" with the missing evidence
// named; no score is drawn for a position the API carries no current game
// for; images come only from the page's host or a named allowlist and load.
// The ask is checked separately: the API carries it, and whether the page
// shows it is recorded, not assumed.
'use strict';
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.argv[2];
const OUT = process.argv[3];
const API = '/api/command/paper/trader-mode';
const PHONE = { isMobile: true, hasTouch: true };
const DEVICES = {
  desktop: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false },
  iphone: Object.assign({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3 }, PHONE),
  iphone_landscape: Object.assign({ viewport: { width: 844, height: 390 }, deviceScaleFactor: 3 }, PHONE),
  ipad_portrait: Object.assign({ viewport: { width: 820, height: 1180 }, deviceScaleFactor: 2 }, PHONE),
  ipad_landscape: Object.assign({ viewport: { width: 1180, height: 820 }, deviceScaleFactor: 2 }, PHONE),
};
// hosts an image may come from besides the page's own (team / venue marks in
// the authored V4 package and the Live Game State provider allowlist); any
// other host is a finding
const IMAGE_HOSTS = ['a.espncdn.com', 'polymarket.com'];
const STANDING = ['RESTING', 'PARTIALLY_FILLED', 'CANCEL_PENDING', 'PENDING_SIMULATION'];
const FOCUS_ALL_ON = 'desktop';     // every position's focus desk on desktop
const FOCUS_SAMPLE = 6;             // the first N elsewhere

// the page's own display formats (trader-core.js), restated so the check
// does not run the code it is checking
const finite = v => typeof v === 'number' && Number.isFinite(v);
const fmt = (n, d = 0) => finite(n) ? new Intl.NumberFormat('en-US', { maximumFractionDigits: d, minimumFractionDigits: d }).format(n) : '—';
const cents = p => finite(p) ? `${fmt(p * 100, 1)}¢` : '—';
const money = (v, signed = false) => finite(v) ? `${signed ? (v < 0 ? '−' : v > 0 ? '+' : '') : v < 0 ? '−' : ''}$${fmt(Math.abs(v), 2)}` : '—';
const standing = p => (p.orders || []).filter(o => STANDING.includes(o.state));
const target = p => { const os = standing(p); return os.find(o => o.direction === 'SELL' && ['RESTING', 'PARTIALLY_FILLED'].includes(o.state)) || os.find(o => o.direction === 'SELL') || os[0] || p.proposal || null; };
const gameCurrent = g => !!(g && g.status === 'CURRENT');

function readDom() {
  const T = el => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
  const vis = el => { if (!el) return false; const s = getComputedStyle(el); const r = el.getBoundingClientRect(); return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0; };
  const cards = [...document.querySelectorAll('#positions .position-card')].map(c => {
    const blocks = [...c.querySelectorAll('.card-prices .price-block')];
    const bidB = blocks.find(b => !b.classList.contains('target') && !b.classList.contains('pnl'));
    const tgtB = blocks.find(b => b.classList.contains('target'));
    const pnlB = blocks.find(b => b.classList.contains('pnl'));
    const scoreNums = [...c.querySelectorAll('.team-score,[data-live-score-number]')].map(T).filter(t => t && /\d/.test(t));
    const clocks = [...c.querySelectorAll('[data-game-clock],.live-score-clock,.game-clock')].map(T).filter(Boolean);
    return { id: c.dataset.id, bid: T(bidB && bidB.querySelector('strong')), bid_label: T(bidB && bidB.querySelector('small')),
      bid_visible: vis(bidB && bidB.querySelector('strong')), target: T(tgtB && tgtB.querySelector('strong')),
      pnl: T(pnlB && pnlB.querySelector('strong')), strip: T(c.querySelector('.order-strip')),
      score_numbers: scoreNums, clocks, text: T(c).slice(0, 2000) };
  });
  const imgs = [...document.querySelectorAll('img')].map(i => { let host = null; try { host = new URL(i.currentSrc || i.src, location.href).host; } catch (e) {}
    return { host, same_origin: host === location.host, complete: i.complete, natural_w: i.naturalWidth, visible: vis(i), alt: i.getAttribute('alt') }; });
  return {
    preview_flag: window.__TRADER_PREVIEW__ === true,
    connection: T(document.getElementById('connection')), footer: T(document.getElementById('footer-proof')),
    error_shown: (() => { const e = document.getElementById('error'); return !!(e && !e.hidden && vis(e)); })(),
    wall_count: T(document.getElementById('wall-count')), order_count: T(document.getElementById('order-count')),
    metrics: T(document.getElementById('metrics')), truncate_banner: !!document.querySelector('#positions .truncate-banner'),
    cards, imgs, overflow_px: document.documentElement.scrollWidth - innerWidth,
    tilde_clocks: [...document.querySelectorAll('body *')].filter(e => e.children.length === 0 && /^~\d+:\d\d$/.test((e.textContent || '').trim())).length,
  };
}

function compareCards(S, dom) {
  const byId = new Map(S.positions.map(p => [p.position_id, p]));
  const domIds = new Set(dom.cards.map(c => c.id));
  const out = { missing_cards: [...byId.keys()].filter(id => !domIds.has(id)), extra_cards: [...domIds].filter(id => !byId.has(id)),
    bid_mismatch: [], target_mismatch: [], pnl_mismatch: [], strip_mismatch: [], score_without_current_game: [], score_mismatch: [],
    bid_hidden: 0, ask_in_api: 0, ask_shown: 0 };
  for (const c of dom.cards) {
    const p = byId.get(c.id); if (!p) continue;
    const q = p.quote || {}, t = target(p);
    if (c.bid !== cents(q.bid)) out.bid_mismatch.push({ id: c.id, dom: c.bid, api: cents(q.bid) });
    if (!c.bid_visible) out.bid_hidden++;
    if (c.target !== cents(t && t.limit_price)) out.target_mismatch.push({ id: c.id, dom: c.target, api: cents(t && t.limit_price) });
    if (c.pnl !== money(p.unrealized_usd, true)) out.pnl_mismatch.push({ id: c.id, dom: c.pnl, api: money(p.unrealized_usd, true) });
    const want = t ? `${t.order_id ? 'Recorded' : 'Proposed'} ${t.direction} ${cents(t.limit_price)}` : 'No standing order or proposed target recorded';
    if (!(c.strip || '').includes(want) || (t && !t.order_id && !(c.strip || '').includes('NOT PLACED')))
      out.strip_mismatch.push({ id: c.id, dom: c.strip, want });
    if (!gameCurrent(p.game) && c.score_numbers.length) out.score_without_current_game.push({ id: c.id, shown: c.score_numbers });
    if (gameCurrent(p.game) && c.score_numbers.length) {
      const want2 = [p.game.away_score, p.game.home_score].filter(finite).map(v => fmt(v));
      if (want2.some(v => !c.score_numbers.includes(v))) out.score_mismatch.push({ id: c.id, shown: c.score_numbers, api: want2 });
    }
    if (finite(q.ask)) { out.ask_in_api++; if ((c.text || '').includes(cents(q.ask)) && cents(q.ask) !== cents(q.bid)) out.ask_shown++; }
  }
  return out;
}

const motionKeys = c => [c.bid, c.target, c.pnl, c.strip, (c.score_numbers || []).join('/')].join(' | ');

(async () => {
  fs.mkdirSync(path.join(OUT, 'trader'), { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined, args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const results = [];
  for (const [dev, d] of Object.entries(DEVICES)) {
    const ctx = await browser.newContext(Object.assign({}, d));
    let frozenBody = null, live = 0, frozenServed = 0, aborted = 0; let S = null; const errors = [];
    await ctx.route('**/*', async route => {
      const req = route.request(); const m = req.method();
      if (m !== 'GET' && m !== 'HEAD') { aborted++; return route.abort(); }
      if (frozenBody && new URL(req.url()).pathname === API) { frozenServed++; return route.fulfill({ status: 200, contentType: 'application/json', body: frozenBody }); }
      return route.continue();
    });
    const page = await ctx.newPage();
    page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 200)));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
    page.on('response', async r => {
      if (frozenBody || new URL(r.url()).pathname !== API || r.status() !== 200) return;
      try { const txt = await r.text(); const j = JSON.parse(txt); if (j && Array.isArray(j.positions)) { S = j; live++; } } catch (e) {}
    });
    const rec = { device: dev, viewport: d.viewport };
    try { await page.goto(BASE + '/trader', { waitUntil: 'load', timeout: 60000 }); } catch (e) { errors.push('goto: ' + String(e).slice(0, 160)); }
    await page.waitForTimeout(12000);
    rec.live_snapshots = live;
    if (!S) {
      rec.verdict = 'NO_API_SNAPSHOT'; rec.errors = errors.slice(0, 8);
      await page.screenshot({ path: path.join(OUT, 'trader', dev + '_no_snapshot.png') }).catch(() => {});
      results.push(rec); await ctx.close(); continue;
    }
    // FROZEN: answer every further poll with S, wait for the page to render one
    frozenBody = JSON.stringify(S);
    const t0 = Date.now(); while (frozenServed < 1 && Date.now() - t0 < 30000) await page.waitForTimeout(500);
    await page.waitForTimeout(1500);
    const d1 = await page.evaluate(readDom);
    await page.waitForTimeout(10000);
    const d2 = await page.evaluate(readDom);
    rec.frozen_polls_served = frozenServed;
    const totalStanding = S.positions.reduce((n, p) => n + standing(p).length, 0);
    rec.api = { total_position_count: S.total_position_count, returned: S.positions.length, truncated: S.truncated,
      standing_orders: totalStanding, with_current_game: S.positions.filter(p => gameCurrent(p.game)).length,
      score_feed: S.score_feed || null, source_sha: S.source_sha || null, mode: S.mode, authority: S.authority };
    rec.page = { preview_flag: d1.preview_flag, connection: d1.connection, footer_prefix: (d1.footer || '').split('·')[0].trim(),
      error_shown: d1.error_shown, wall_count: d1.wall_count, order_count: d1.order_count, cards: d1.cards.length,
      truncate_banner: d1.truncate_banner, overflow_px: d1.overflow_px, tilde_clocks: d1.tilde_clocks };
    rec.fidelity = compareCards(S, d1);
    // MOTION while the data is frozen
    const m1 = new Map(d1.cards.map(c => [c.id, motionKeys(c)]));
    rec.moved_while_frozen = d2.cards.filter(c => m1.has(c.id) && m1.get(c.id) !== motionKeys(c)).map(c => ({ id: c.id, before: m1.get(c.id), after: motionKeys(c) })).slice(0, 20);
    rec.counts_moved_while_frozen = d1.wall_count !== d2.wall_count || d1.order_count !== d2.order_count || d1.cards.length !== d2.cards.length;
    // images
    rec.images = { total: d1.imgs.length,
      foreign_hosts: [...new Set(d1.imgs.filter(i => i.host && !i.same_origin && !IMAGE_HOSTS.includes(i.host)).map(i => i.host))],
      hosts: [...new Set(d1.imgs.map(i => i.same_origin ? 'self' : i.host))],
      broken_visible: d1.imgs.filter(i => i.visible && i.complete && i.natural_w === 0).length };
    await page.screenshot({ path: path.join(OUT, 'trader', dev + '_wall.png') }).catch(() => {});
    await page.screenshot({ path: path.join(OUT, 'trader', dev + '_wall_full.png'), fullPage: true }).catch(() => {});
    // FOCUS DESK: recorded bid and recorded management for each position
    const ids = d1.cards.map(c => c.id);
    const pick = dev === FOCUS_ALL_ON ? ids : ids.slice(0, FOCUS_SAMPLE);
    const focus = { checked: 0, bid_mismatch: [], fabricated_recommendation: [], blocked_without_missing: [], recommendation_shown: 0, waiting: 0 };
    const byId = new Map(S.positions.map(p => [p.position_id, p]));
    for (const id of pick) {
      const ok = await page.evaluate(id => { const b = [...document.querySelectorAll('#positions .card-focus')].find(e => e.dataset.focus === id); if (!b) return false; b.click(); return true; }, id);
      if (!ok) continue;
      await page.waitForTimeout(120);
      const f = await page.evaluate(() => {
        const T = el => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
        const v = document.querySelector('#focus-market .screen-value');
        return { bid: v && v.firstChild ? (v.firstChild.textContent || '').trim() : null,
          action: T(document.querySelector('#focus-decision .screen-decision')), note: T(document.querySelector('#focus-decision .screen-decision-note')) };
      });
      const p = byId.get(id); focus.checked++;
      if (f.bid !== cents((p.quote || {}).bid)) focus.bid_mismatch.push({ id, dom: f.bid, api: cents((p.quote || {}).bid) });
      const recorded = [p.packet && p.packet.current_recommendation, p.review && p.review.recommendation].filter(Boolean);
      if (f.action === 'Waiting for evidence') { focus.waiting++; if (!/Missing:/.test(f.note || '')) focus.blocked_without_missing.push({ id, note: f.note }); }
      else if (f.action === 'REVIEW RECORDED' ? !(p.review && p.review.review_id) : !recorded.includes(f.action)) focus.fabricated_recommendation.push({ id, shown: f.action, recorded });
      else focus.recommendation_shown++;
      if (focus.checked === 1) await page.screenshot({ path: path.join(OUT, 'trader', dev + '_focus.png') }).catch(() => {});
    }
    rec.focus = focus;
    // ORDERS VIEW: exactly the standing orders, their limits and states
    const ov = await page.evaluate(() => { const b = document.querySelector('[data-view="orders"]'); if (!b) return null; b.click(); return true; });
    if (ov) {
      await page.waitForTimeout(400);
      const rows = await page.evaluate(() => [...document.querySelectorAll('#positions .orders-view tbody tr')].map(tr => [...tr.children].map(td => (td.textContent || '').replace(/\s+/g, ' ').trim())));
      const real = rows.filter(r => r.length >= 7);
      const want = S.positions.flatMap(p => standing(p).map(o => ({ limit: cents(o.limit_price), state: o.state })));
      const multiset = a => a.reduce((m, x) => (m[x] = (m[x] || 0) + 1, m), {});
      const wantKeys = multiset(want.map(o => o.limit + ' ' + o.state));
      const gotKeys = multiset(real.map(r => r[4] + ' ' + (r[6] || '').replace('PAPER SIMULATED', '').trim()));
      rec.orders_view = { rows: real.length, api_standing: want.length,
        limit_state_multiset_equal: JSON.stringify(Object.entries(wantKeys).sort()) === JSON.stringify(Object.entries(gotKeys).sort()),
        every_row_labelled_paper_simulated: real.every(r => /PAPER SIMULATED/.test(r[6] || '')) };
      await page.screenshot({ path: path.join(OUT, 'trader', dev + '_orders.png') }).catch(() => {});
    } else rec.orders_view = null;
    rec.non_get_aborted = aborted; rec.console_errors = errors.length; rec.first_errors = errors.slice(0, 6);
    const F = rec.fidelity;
    const fails = [];
    if (rec.page.preview_flag || rec.page.footer_prefix !== 'READ ONLY' || !/Readback connected/.test(rec.page.connection || '')) fails.push('NOT_THE_NATIVE_READBACK');
    if (rec.page.error_shown) fails.push('ERROR_BANNER_SHOWN');
    if (F.missing_cards.length || F.extra_cards.length) fails.push('POSITION_SET_DIFFERS');
    if (String(S.total_position_count) !== rec.page.wall_count) fails.push('WALL_COUNT_DIFFERS');
    if (String(totalStanding) !== rec.page.order_count) fails.push('ORDER_COUNT_DIFFERS');
    if (!!S.truncated !== rec.page.truncate_banner) fails.push('TRUNCATION_NOT_DISCLOSED');
    if (F.bid_mismatch.length) fails.push('BID_DIFFERS');
    if (F.target_mismatch.length) fails.push('TARGET_DIFFERS');
    if (F.pnl_mismatch.length) fails.push('PNL_DIFFERS');
    if (F.strip_mismatch.length) fails.push('ORDER_STRIP_DIFFERS');
    if (F.score_without_current_game.length) fails.push('SCORE_WITHOUT_CURRENT_GAME');
    if (F.score_mismatch.length) fails.push('SCORE_DIFFERS');
    if (F.bid_hidden) fails.push('BID_NOT_VISIBLE');
    if (rec.moved_while_frozen.length || rec.counts_moved_while_frozen) fails.push('MOVED_WITHOUT_DATA');
    if (rec.page.tilde_clocks) fails.push('ESTIMATED_CLOCK_SHOWN');
    if (rec.images.foreign_hosts.length) fails.push('IMAGE_FROM_UNLISTED_HOST');
    if (rec.images.broken_visible) fails.push('BROKEN_IMAGE_VISIBLE');
    if (focus.bid_mismatch.length) fails.push('FOCUS_BID_DIFFERS');
    if (focus.fabricated_recommendation.length) fails.push('RECOMMENDATION_NOT_RECORDED');
    if (focus.blocked_without_missing.length) fails.push('BLOCKED_WITHOUT_REASON');
    if (!rec.orders_view || rec.orders_view.rows !== rec.orders_view.api_standing || !rec.orders_view.limit_state_multiset_equal || !rec.orders_view.every_row_labelled_paper_simulated) fails.push('ORDERS_VIEW_DIFFERS');
    if (rec.page.overflow_px > 0) fails.push('HORIZONTAL_OVERFLOW');
    if (F.ask_in_api && F.ask_shown < F.ask_in_api) fails.push('ASK_NOT_SHOWN');
    if (rec.frozen_polls_served < 1) fails.push('FROZEN_PHASE_NOT_REACHED');
    rec.failures = fails; rec.verdict = fails.length ? 'FAIL' : 'PASS';
    results.push(rec);
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, 'trader_accept.json'), JSON.stringify({ base: BASE, at: new Date().toISOString(), results }, null, 1));
  for (const r of results) {
    const a = r.api || {}, p = r.page || {}, F = r.fidelity || {};
    console.log(`TRADER ${r.device.padEnd(17)} ${r.verdict} api=${a.total_position_count}/${a.returned} orders=${a.standing_orders} games=${a.with_current_game} | page cards=${p.cards} wall=${p.wall_count} orders=${p.order_count} | missing=${(F.missing_cards || []).length} bid!=${(F.bid_mismatch || []).length} tgt!=${(F.target_mismatch || []).length} pnl!=${(F.pnl_mismatch || []).length} strip!=${(F.strip_mismatch || []).length} ask=${F.ask_shown}/${F.ask_in_api} moved=${(r.moved_while_frozen || []).length} focus=${r.focus ? r.focus.checked : 0} ov=${r.orders_view ? r.orders_view.rows + '/' + r.orders_view.api_standing : 'none'} imgs=${JSON.stringify((r.images || {}).hosts)} | ${JSON.stringify(r.failures || [])}`);
  }
})();
