// PLATFORM CAPTURE (read only), for platform-capture.yml.
//
// Screen-records the real COMMAND frontend served by the preview server
// (serve.js: the target ref's own build, /api/command/* proxied GET-only to
// production with the read token added server side). Two modes:
//
//   discover  every route in ROUTES at 1920x1080: a screenshot, the visible
//             clickable elements (text, href, a usable selector, box), the
//             headings and the page text, so a walkthrough can be planned
//             against the data production actually holds
//   record    runs a plan (JSON steps) in ONE tab while Chromium's own
//             compositor frames are captured over CDP (Page.startScreencast,
//             JPEG q92, 1920x1080) with their timestamps; assemble.py turns
//             them into a constant-30 fps H.264 file. Clicks are real
//             (page.mouse at the element's centre after a visible, smooth
//             cursor move); scrolling is real wheel input. A small cursor
//             overlay is drawn because headless Chromium paints no pointer.
//
// Nothing here writes: every non-GET is aborted in the browser and refused
// by the server. Real pages get no fixture and no injected data.
//
// ILLUSTRATIVE PAGES ARE KEPT APART FROM REAL DATA. A page opened with
// ?demo=1 (the classic Command's own opt-in design preview) or
// ?illustrative=1 (Trader's own ILLUSTRATIVE REPLAY mode, fed the SAME
// design fixture, BTCore.demoSnapshot() from the served core.js, mapped to
// the Trader schema; no number is added) is ISOLATED: every /api/ read it
// makes is aborted, so real and illustrative figures never share a screen,
// and a caption is burned into the frame: ILLUSTRATIVE -- HYPOTHETICAL
// DATA, NOT ACTUAL RESULTS. The workflow encrypts everything this writes
// before upload (the repository is public).
'use strict';
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const [BASE, OUT, MODE, PLAN] = process.argv.slice(2);
const VIEW = { width: 1920, height: 1080 };
const ROUTES = ['/', '/floor', '/scout', '/derek', '/karen', '/allocator', '/archer', '/xavier',
  '/audrey', '/eddie', '/positions', '/trader', '/ops', '/readiness', '/red-team', '/profitability',
  '/venues', '/revenue', '/acceptance', '/improvements', '/company',
  '/classic.html?demo=1', '/classic.html?demo=1&reads=1', '/trader?illustrative=1'];
const ILLUSTRATIVE = /[?&](demo|illustrative)=1\b/;
// discovery only: reads=1 lets an illustrative page make its reads, to see
// exactly which real fields would appear beside the fixture
const READS_ALLOWED = /[?&]reads=1\b/;

const CURSOR = `(() => {
  if (window.__capCursor) return; window.__capCursor = true;
  const add = () => {
    const c = document.createElement('div'); c.id = '__cap_cursor';
    c.style.cssText = 'position:fixed;left:0;top:0;width:22px;height:22px;z-index:2147483647;pointer-events:none;transform:translate(-100px,-100px);transition:none';
    c.innerHTML = '<svg width="22" height="22" viewBox="0 0 22 22"><path d="M2 2 L2 18 L6.5 13.8 L9.6 20.6 L12.4 19.4 L9.4 12.7 L15.5 12.4 Z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(c);
    addEventListener('mousemove', e => { c.style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px)'; }, true);
    addEventListener('mousedown', e => { const r = document.createElement('div');
      r.style.cssText = 'position:fixed;left:' + (e.clientX - 14) + 'px;top:' + (e.clientY - 14) + 'px;width:28px;height:28px;border-radius:50%;border:2px solid rgba(255,255,255,.9);box-shadow:0 0 0 2px rgba(0,0,0,.35);z-index:2147483646;pointer-events:none;transition:transform .45s ease-out,opacity .45s ease-out';
      document.documentElement.appendChild(r); requestAnimationFrame(() => { r.style.transform = 'scale(1.8)'; r.style.opacity = '0'; }); setTimeout(() => r.remove(), 600); }, true);
  };
  if (document.documentElement) add(); else addEventListener('DOMContentLoaded', add);
  if (/[?&](demo|illustrative)=1\\b/.test(location.search)) {
    const cap = () => { const b = document.createElement('div'); b.id = '__cap_illustrative';
      b.style.cssText = 'position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:2147483645;pointer-events:none;padding:9px 20px;border-radius:8px;background:rgba(150,20,20,.92);color:#fff;font:700 17px/1.2 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;letter-spacing:.06em;box-shadow:0 2px 10px rgba(0,0,0,.45);white-space:nowrap';
      b.textContent = 'ILLUSTRATIVE \u2014 HYPOTHETICAL DATA, NOT ACTUAL RESULTS'; document.documentElement.appendChild(b); };
    if (document.documentElement) cap(); else addEventListener('DOMContentLoaded', cap);
  }
})();`;

// Trader's own ILLUSTRATIVE REPLAY mode, fed the classic Command's design
// fixture (BTCore.demoSnapshot) mapped field for field: same positions,
// quantities, entries, marks, unrealized results and SELL orders. Nothing is
// added: no ask (the fixture has none), no price history beyond the one mark,
// no alternatives. Times are re-stamped relative to the moment of capture so
// the replay renders as current, which is what a replay is.
async function illustrativeTrader(base) {
  const res = await fetch(base + '/core.js');   // the command host serves /command/* at /
  if (!res.ok) throw new Error('core.js HTTP ' + res.status);
  const code = await res.text();
  const sandbox = { window: {}, module: undefined, globalThis: {} };
  vm.runInNewContext(code.replace(/\(typeof window==='undefined'\?globalThis:window\)\s*;?\s*$/, '(window);'), sandbox);
  const C = sandbox.window.BTCore;
  if (!C || typeof C.demoSnapshot !== 'function') throw new Error('core.js demoSnapshot not found');
  const d = C.demoSnapshot();
  const venue = v => /polymarket/i.test(v) ? 'POLYMARKET_US' : String(v).toUpperCase();
  const positions = d.positions.map((p, i) => ({
    position_id: 'demo:' + p.id, title: p.title, market_title: p.outcome, market_id: p.marketId,
    event_id: p.eventId, venue: venue(p.venue), sport: String(p.sport).toUpperCase(), family: String(p.family).toUpperCase(),
    holding_side: 'LONG', qty: p.quantity, entry_price: p.entry, cost_basis_usd: p.cost, state: 'ACTIVE', game: null, proposal: null,
    quote: { bid: p.mark, ask: null, at_offset: -(4 + i), current: true, observation_id: 'demo-obs-' + p.id },
    quote_history: [{ at_offset: -(4 + i), price: p.mark, observation_id: 'demo-obs-' + p.id }],
    packet: { complete: true, missing: [], probability_at_offset: -2, probability_current: true, current_recommendation: p.intent },
    review: { review_id: 'demo-review-' + p.id, recommendation: p.intent, reviewed_at_offset: -40 - i * 7, alternatives: { candidates: [] } },
    orders: d.orders.filter(o => o.positionId === p.id && o.side === 'SELL' && /RESTING|PARTIAL/.test(o.status)).map(o => ({
      order_id: o.id, position_id: 'demo:' + p.id, direction: 'SELL', limit_price: o.price,
      state: o.status === 'PARTIAL' ? 'PARTIALLY_FILLED' : 'RESTING', role: 'EXIT',
      remaining_qty: o.quantity - o.filled, is_standing: true })),
    unrealized_usd: p.unrealized, fixture_basis: p.markBasis }));
  return { schema: 'bettor.trader.v1', mode: 'DEMO', source: 'ILLUSTRATIVE', authority: 'NONE', execution_authority: false,
    snapshot_id: d.snapshotId, fixture: d.provenance && d.provenance.sourceId, disclaimer: d.provenance && d.provenance.disclaimer,
    positions, total_position_count: positions.length, returned_position_count: positions.length, truncated: false };
}

function traderInit(fixture) {
  return `(() => {
    if (!/\\/trader(\\.html)?$/.test(location.pathname) || !/[?&]illustrative=1\\b/.test(location.search)) return;
    const f = ${JSON.stringify(fixture)}; const now = Date.now() / 1000;
    const stamp = o => { if (o && typeof o === 'object') for (const k of Object.keys(o)) {
      if (k.endsWith('_offset') && typeof o[k] === 'number') { o[k.slice(0, -7)] = now + o[k]; delete o[k]; } else stamp(o[k]); } return o; };
    stamp(f); f.snapshot_at = now - 1;
    window.__TRADER_PREVIEW__ = true; window.__TRADER_DEMO_INITIAL__ = f;
  })();`;
}

const ISOLATED = { aborted_reads: 0 };
async function newContext(browser) {
  const ctx = await browser.newContext({ viewport: VIEW, deviceScaleFactor: 1, isMobile: false, hasTouch: false,
    reducedMotion: 'no-preference', colorScheme: 'dark' });
  await ctx.route('**/*', route => {
    const req = route.request(); const m = req.method();
    if (m !== 'GET' && m !== 'HEAD') return route.abort();
    let pageUrl = ''; try { pageUrl = req.frame().url(); } catch (e) {}
    if (ILLUSTRATIVE.test(pageUrl) && !READS_ALLOWED.test(pageUrl) && new URL(req.url()).pathname.startsWith('/api/') && !req.isNavigationRequest()) { ISOLATED.aborted_reads++; return route.abort(); }
    return route.continue();
  });
  await ctx.addInitScript(CURSOR);
  await ctx.addInitScript(traderInit(await illustrativeTrader(BASE)));
  return ctx;
}

async function discover(browser) {
  fs.mkdirSync(path.join(OUT, 'discover'), { recursive: true });
  const ctx = await newContext(browser);
  const page = await ctx.newPage();
  for (const r of ROUTES) {
    const name = r === '/' ? 'command' : r.slice(1).replace(/\W+/g, '_');
    const before = ISOLATED.aborted_reads;
    let status = null;
    try { const resp = await page.goto(BASE + r, { waitUntil: 'load', timeout: 60000 }); status = resp && resp.status(); } catch (e) {}
    await page.waitForTimeout(9000);
    const info = await page.evaluate(() => {
      const vis = el => { const s = getComputedStyle(el); const b = el.getBoundingClientRect(); return s.visibility !== 'hidden' && s.display !== 'none' && b.width > 0 && b.height > 0; };
      const sel = el => { if (el.id) return '#' + CSS.escape(el.id); const parts = []; let e = el;
        while (e && e.nodeType === 1 && parts.length < 5) { let p = e.tagName.toLowerCase(); if (e.id) { parts.unshift('#' + CSS.escape(e.id)); break; }
          const cls = [...e.classList].filter(c => !/^(active|focused|on|open|is-)/.test(c)).slice(0, 2); if (cls.length) p += '.' + cls.map(c => CSS.escape(c)).join('.');
          const sib = e.parentElement ? [...e.parentElement.children].filter(x => x.tagName === e.tagName) : []; if (sib.length > 1) p += ':nth-of-type(' + (sib.indexOf(e) + 1) + ')';
          parts.unshift(p); e = e.parentElement; } return parts.join(' > '); };
      const clickables = [...document.querySelectorAll('a[href],button,[role=button],[role=tab],[data-focus],[data-id],summary,[onclick],[data-action],[data-view],[data-filter]')]
        .filter(vis).slice(0, 400).map(el => { const b = el.getBoundingClientRect(); const data = {}; for (const a of el.attributes) if (a.name.startsWith('data-')) data[a.name] = a.value.slice(0, 120);
          return { tag: el.tagName.toLowerCase(), text: (el.innerText || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 90),
            href: el.getAttribute('href'), sel: sel(el), data, box: [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)] }; });
      return { title: document.title, url: location.pathname + location.search, scroll_h: document.documentElement.scrollHeight,
        headings: [...document.querySelectorAll('h1,h2,h3')].filter(vis).map(h => h.innerText.replace(/\s+/g, ' ').trim().slice(0, 120)).slice(0, 80),
        clickables, text: (document.body.innerText || '').slice(0, 20000),
        canvases: [...document.querySelectorAll('canvas')].filter(vis).map(c => ({ engine: c.getAttribute('data-engine'), w: c.width, h: c.height })) };
    }).catch(e => ({ error: String(e).slice(0, 200) }));
    info.status = status; info.route = r; info.isolated_reads_aborted = ISOLATED.aborted_reads - before;
    fs.writeFileSync(path.join(OUT, 'discover', name + '.json'), JSON.stringify(info, null, 1));
    await page.screenshot({ path: path.join(OUT, 'discover', name + '.png') }).catch(() => {});
    await page.screenshot({ path: path.join(OUT, 'discover', name + '_full.png'), fullPage: true }).catch(() => {});
    console.log('discovered', r, 'HTTP', status, 'clickables', (info.clickables || []).length);
  }
  await ctx.close();
}

// -- recording ------------------------------------------------------------
let mouse = { x: VIEW.width / 2, y: VIEW.height / 2 };
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function locate(page, t) {
  // a target is {sel} or {text, within?, nth?}; returns the element's centre
  let loc;
  if (t.sel) loc = page.locator(t.sel);
  else if (t.text) loc = (t.within ? page.locator(t.within) : page).getByText(t.text, { exact: !!t.exact });
  else if (t.role) loc = page.getByRole(t.role, { name: t.name, exact: !!t.exact });
  if (!loc) throw new Error('no target');
  loc = loc.nth(t.nth || 0);
  await loc.scrollIntoViewIfNeeded({ timeout: 8000 });
  const b = await loc.boundingBox({ timeout: 8000 });
  if (!b) throw new Error('target not visible');
  return { x: b.x + b.width / 2, y: b.y + Math.min(b.height / 2, 18) };
}

async function glide(page, x, y, ms = 900) {
  // time-bounded: on a busy page each input event waits for the main thread,
  // so the path is sampled by elapsed time and always ends on the target
  const sx = mouse.x, sy = mouse.y, t0 = Date.now();
  for (;;) {
    const k = Math.min(1, (Date.now() - t0) / ms), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    await page.mouse.move(sx + (x - sx) * e, sy + (y - sy) * e);
    if (k >= 1) break;
    await sleep(16);
  }
  mouse = { x, y };
}

async function wheel(page, dy, ms = 2000) {
  // real wheel input spread over ms (time-bounded like glide)
  const t0 = Date.now(); let done = 0;
  for (;;) {
    const k = Math.min(1, (Date.now() - t0) / ms), want = Math.round(dy * (k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2));
    if (want !== done) { await page.mouse.wheel(0, want - done); done = want; }
    if (k >= 1) break;
    await sleep(30);
  }
}

async function record(browser, plan) {
  const frames = path.join(OUT, 'frames');
  fs.mkdirSync(frames, { recursive: true });
  const ctx = await newContext(browser);
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  const index = []; let n = 0; let pending = Promise.resolve();
  cdp.on('Page.screencastFrame', f => {
    const i = n++; const file = String(i).padStart(6, '0') + '.jpg';
    pending = pending.then(() => fs.promises.writeFile(path.join(frames, file), Buffer.from(f.data, 'base64')));
    index.push({ file, t: f.metadata.timestamp });
    cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});
  });
  const log = []; const t0 = Date.now();
  const mark = (kind, detail) => { const e = { t_s: +((Date.now() - t0) / 1000).toFixed(2), utc: new Date().toISOString(), kind, detail }; log.push(e); console.log(e.t_s.toFixed(1).padStart(7), kind, typeof detail === 'string' ? detail : JSON.stringify(detail)); };
  // the first screen is loaded before the capture starts, so the file opens on the product
  const first = plan.find(s => s.goto);
  if (first) { await page.goto(BASE + first.goto, { waitUntil: 'load', timeout: 60000 }).catch(e => mark('error', String(e).slice(0, 120))); await sleep(first.settle || 8000); }
  await page.mouse.move(mouse.x, mouse.y);
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, maxWidth: VIEW.width, maxHeight: VIEW.height, everyNthFrame: 1 });
  mark('capture_start', BASE);
  let skippedFirstGoto = false;
  for (const s of plan) {
    try {
      if (s.chapter) mark('chapter', s.chapter);
      else if (s.goto) { if (!skippedFirstGoto && s === first) { skippedFirstGoto = true; continue; } mark('goto', s.goto); await page.goto(BASE + s.goto, { waitUntil: 'load', timeout: 60000 }); await page.mouse.move(mouse.x, mouse.y); }
      else if (s.wait) await sleep(s.wait);
      else if (s.move) { const p = await locate(page, s.move); await glide(page, p.x + (s.move.dx || 0), p.y + (s.move.dy || 0), s.ms || 900); }
      else if (s.moveTo) await glide(page, s.moveTo[0], s.moveTo[1], s.ms || 900);
      else if (s.click) { const p = await locate(page, s.click); await glide(page, p.x, p.y, s.ms || 900); await sleep(250); mark('click', s.click.text || s.click.sel || s.click.name); await page.mouse.down(); await sleep(90); await page.mouse.up(); if (s.after) await sleep(s.after); }
      else if (s.scroll) await wheel(page, s.scroll.dy, s.scroll.ms || 2000);
      else if (s.scrollTop) { await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' })); await sleep(s.scrollTop); }
      else if (s.key) { mark('key', s.key); await page.keyboard.press(s.key); }
      else if (s.drag) { const [x1, y1, x2, y2] = s.drag; await glide(page, x1, y1, 700); await page.mouse.down(); await glide(page, x2, y2, s.ms || 1600); await page.mouse.up(); mark('drag', s.drag); }
      else if (s.note) mark('note', s.note);
      if (s.hold) await sleep(s.hold);
    } catch (e) { mark(s.optional ? 'skipped' : 'step_failed', { step: s, error: String(e).slice(0, 160) }); }
  }
  mark('capture_end', null);
  await cdp.send('Page.stopScreencast').catch(() => {});
  await sleep(500); await pending;
  fs.writeFileSync(path.join(OUT, 'frames.json'), JSON.stringify(index));
  fs.writeFileSync(path.join(OUT, 'capture_log.json'), JSON.stringify({ base: BASE, viewport: VIEW, frames: index.length, illustrative_isolation: ISOLATED, log }, null, 1));
  await ctx.close();
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined,
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--hide-scrollbars', '--force-color-profile=srgb'] });
  if (MODE === 'discover') await discover(browser);
  else await record(browser, JSON.parse(fs.readFileSync(PLAN, 'utf8')));
  await browser.close();
})();
