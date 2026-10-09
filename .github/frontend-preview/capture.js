// PLATFORM CAPTURE (read only), for platform-capture.yml.
//
// Screen-records the real COMMAND frontend. Two sources:
//   * the PRODUCTION host (BASE = https://command.bettortoken.com): the
//     deployed build behind its own Netlify proxy; the existing read
//     credential is added by this process to that host's /api/command/*
//     GET/HEAD requests only (Playwright request layer). It never enters the
//     page, a URL, a cookie, the DOM or the frames;
//   * a CANDIDATE build served by serve.js (BASE = http://127.0.0.1:8787),
//     which adds the credential server side the same way.
//
// Modes:
//   discover  each route at 1920x1080: screenshot, visible clickables,
//             headings, page text (for planning against real data)
//   record    one tab runs a plan of real interactions; every output frame is
//             an explicit 1920x1080 capture (CDP Page.captureScreenshot) placed
//             on a video timeline that assemble.py turns into constant-30 fps
//             H.264. The runner has no GPU (software WebGL), so:
//               * holds on flat pages are REAL TIME, 1:1 (live updates appear
//                 when they happen);
//               * cursor glides and wheel scrolls are FRAME-STEPPED: one
//                 small real input per 1/30 s frame, captured;
//               * on 3D pages ("vt": true) the page's animation clock (the
//                 requestAnimationFrame timestamp and performance.now) is a
//                 VIRTUAL clock advanced exactly 1/30 s per captured frame, so
//                 the product's own animation renders at its designed speed.
//                 Date stays real: on-screen clocks and ages stay truthful.
//             Clicks are real mouse input at the element after a visible
//             cursor glide. A small cursor is drawn because headless Chromium
//             paints none.
//
// NOTHING IS INJECTED INTO THE PRODUCT: no fixture, no mocked response, no
// demo switch. Every non-GET is aborted. Plan values may be remembered from
// the page ($NAME) to follow one record across pages; resolved values go only
// to capture_log.json, which the workflow encrypts with everything else (the
// repository is public). stdout carries step numbers and kinds only.
'use strict';
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const [BASE, OUT, MODE, PLAN] = process.argv.slice(2);
const TOKEN = process.env.READ_TOKEN || '';
const PROD = 'https://command.bettortoken.com';
const VIEW = { width: 1920, height: 1080 };
const ROUTES = ['/', '/floor', '/scout', '/derek', '/karen', '/allocator', '/archer', '/eddie', '/xavier',
  '/audrey', '/positions', '/trader', '/ops', '/readiness', '/red-team', '/venues'];

const CURSOR = `(() => {
  if (window.__capCursor) return; window.__capCursor = true;
  const add = () => {
    const c = document.createElement('div'); c.id = '__cap_cursor';
    c.style.cssText = 'position:fixed;left:0;top:0;width:22px;height:22px;z-index:2147483647;pointer-events:none;transform:translate(-100px,-100px)';
    c.innerHTML = '<svg width="22" height="22" viewBox="0 0 22 22"><path d="M2 2 L2 18 L6.5 13.8 L9.6 20.6 L12.4 19.4 L9.4 12.7 L15.5 12.4 Z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(c);
    addEventListener('mousemove', e => { c.style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px)'; }, true);
    addEventListener('mousedown', e => { const r = document.createElement('div');
      r.style.cssText = 'position:fixed;left:' + (e.clientX - 14) + 'px;top:' + (e.clientY - 14) + 'px;width:28px;height:28px;border-radius:50%;border:2px solid rgba(255,255,255,.9);box-shadow:0 0 0 2px rgba(0,0,0,.35);z-index:2147483646;pointer-events:none;transition:transform .45s ease-out,opacity .45s ease-out';
      document.documentElement.appendChild(r); requestAnimationFrame(() => { r.style.transform = 'scale(1.8)'; r.style.opacity = '0'; }); setTimeout(() => r.remove(), 600); }, true);
  };
  if (document.documentElement) add(); else addEventListener('DOMContentLoaded', add);
})();
(() => {
  // the virtual animation clock: dormant (native behaviour) until __vt.start()
  if (window.__vt) return;
  const nRAF = window.requestAnimationFrame.bind(window), nCAF = window.cancelAnimationFrame.bind(window);
  const nNow = performance.now.bind(performance);
  const VT = { on: false, t: 0, q: new Map(), id: 1e9, nativeRAF: nRAF };
  window.__vt = VT;
  performance.now = () => VT.on ? VT.t : nNow();
  window.requestAnimationFrame = cb => { if (!VT.on) return nRAF(cb); const id = ++VT.id; VT.q.set(id, cb); return id; };
  window.cancelAnimationFrame = id => { if (VT.q.has(id)) VT.q.delete(id); else nCAF(id); };
  VT.start = () => { if (VT.on) return; VT.t = nNow(); VT.on = true; };
  VT.step = ms => { VT.t += ms; const cbs = [...VT.q.values()]; VT.q.clear(); for (const cb of cbs) { try { cb(VT.t); } catch (e) {} } return cbs.length; };
  VT.stop = () => { if (!VT.on) return; VT.on = false; const cbs = [...VT.q.values()]; VT.q.clear(); for (const cb of cbs) nRAF(cb); };
})();`;

const STATS = { api_get: 0, api_status: {}, non_get_aborted: 0, credential_added: 0 };
async function newContext(browser) {
  const ctx = await browser.newContext({ viewport: VIEW, screen: VIEW, deviceScaleFactor: 1, isMobile: false, hasTouch: false,
    reducedMotion: 'no-preference', colorScheme: 'dark' });
  await ctx.route('**/*', route => {
    const req = route.request(); const m = req.method();
    if (m !== 'GET' && m !== 'HEAD') { STATS.non_get_aborted++; return route.abort(); }
    const u = new URL(req.url());
    if (u.pathname.startsWith('/api/')) STATS.api_get++;
    // the read credential: production host, /api/command/ only
    if (TOKEN && BASE === PROD && u.origin === PROD && u.pathname.startsWith('/api/command/')) {
      STATS.credential_added++;
      return route.continue({ headers: Object.assign({}, req.headers(), { 'x-admin-token': TOKEN }) });
    }
    return route.continue();
  });
  await ctx.addInitScript(CURSOR);
  return ctx;
}

function watchApi(page) {
  page.on('response', r => { try { const u = new URL(r.url()); if (u.pathname.startsWith('/api/')) {
    const k = String(r.status()); STATS.api_status[k] = (STATS.api_status[k] || 0) + 1; } } catch (e) {} });
}

async function pageInfo(page) {
  return page.evaluate(() => {
    const vis = el => { const s = getComputedStyle(el); const b = el.getBoundingClientRect(); return s.visibility !== 'hidden' && s.display !== 'none' && b.width > 0 && b.height > 0; };
    const sel = el => { if (el.id) return '#' + CSS.escape(el.id); const parts = []; let e = el;
      while (e && e.nodeType === 1 && parts.length < 5) { let p = e.tagName.toLowerCase(); if (e.id) { parts.unshift('#' + CSS.escape(e.id)); break; }
        const cls = [...e.classList].filter(c => !/^(active|focused|on|open|is-)/.test(c)).slice(0, 2); if (cls.length) p += '.' + cls.map(c => CSS.escape(c)).join('.');
        const sib = e.parentElement ? [...e.parentElement.children].filter(x => x.tagName === e.tagName) : []; if (sib.length > 1) p += ':nth-of-type(' + (sib.indexOf(e) + 1) + ')';
        parts.unshift(p); e = e.parentElement; } return parts.join(' > '); };
    const clickables = [...document.querySelectorAll('a[href],button,[role=button],[role=tab],[data-focus],[data-id],summary,[onclick],[data-action],[data-view],[data-filter],[data-agent]')]
      .filter(vis).slice(0, 500).map(el => { const b = el.getBoundingClientRect(); const data = {}; for (const a of el.attributes) if (a.name.startsWith('data-')) data[a.name] = a.value.slice(0, 120);
        return { tag: el.tagName.toLowerCase(), text: (el.innerText || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 90),
          href: el.getAttribute('href'), sel: sel(el), data, box: [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)] }; });
    return { title: document.title, url: location.pathname + location.search, scroll_h: document.documentElement.scrollHeight,
      signed_in: !document.getElementById('command-unlock'),
      headings: [...document.querySelectorAll('h1,h2,h3')].filter(vis).map(h => h.innerText.replace(/\s+/g, ' ').trim().slice(0, 120)).slice(0, 80),
      clickables, text: (document.body.innerText || '').slice(0, 30000),
      canvases: [...document.querySelectorAll('canvas')].filter(vis).map(c => ({ engine: c.getAttribute('data-engine'), w: c.width, h: c.height })) };
  });
}

async function discover(browser) {
  fs.mkdirSync(path.join(OUT, 'discover'), { recursive: true });
  const ctx = await newContext(browser);
  const page = await ctx.newPage(); watchApi(page);
  for (const r of ROUTES) {
    const name = r === '/' ? 'command' : r.slice(1).replace(/\W+/g, '_');
    let status = null;
    try { const resp = await page.goto(BASE + r, { waitUntil: 'load', timeout: 60000 }); status = resp && resp.status(); } catch (e) {}
    await page.waitForTimeout(9000);
    const info = await pageInfo(page).catch(e => ({ error: String(e).slice(0, 200) }));
    info.status = status; info.route = r;
    fs.writeFileSync(path.join(OUT, 'discover', name + '.json'), JSON.stringify(info, null, 1));
    await page.screenshot({ path: path.join(OUT, 'discover', name + '.png') }).catch(() => {});
    console.log('discovered', name, 'HTTP', status);
  }
  await ctx.close();
}

// -- recording ------------------------------------------------------------
let mouse = { x: VIEW.width / 2, y: VIEW.height / 2 };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const VARS = {};
const sub = v => typeof v === 'string' ? v.replace(/\$([A-Z_]+)/g, (_, k) => (k in VARS ? VARS[k] : '$' + k)) : v;

function locator(page, t) {
  let loc;
  if (t.sel) loc = page.locator(sub(t.sel));
  else if (t.text) loc = (t.within ? page.locator(sub(t.within)) : page).getByText(sub(t.text), { exact: !!t.exact });
  else if (t.role) loc = page.getByRole(t.role, { name: sub(t.name), exact: !!t.exact });
  if (!loc) throw new Error('no target');
  if (t.has) loc = loc.filter({ hasText: sub(t.has) });
  return loc.nth(t.nth || 0);
}

async function locate(page, t) {
  const loc = locator(page, t);
  await loc.waitFor({ state: 'visible', timeout: t.timeout || 15000 });
  // no stability wait: on a software-rendered 3D page an element that follows
  // the scene never holds still for two animation frames
  if (t.scrollIntoView !== false) await loc.evaluate(el => { const r = el.getBoundingClientRect();
    if (r.top < 80 || r.bottom > innerHeight - 20) el.scrollIntoView({ block: 'center', inline: 'nearest' }); }).catch(() => {});
  const b = await loc.boundingBox({ timeout: 8000 });
  if (!b) throw new Error('target not visible');
  return { x: b.x + Math.min(b.width / 2, t.xMax || 1e9), y: b.y + Math.min(b.height / 2, 18) };
}

const FPS = 30, DT = 1 / FPS;

async function record(browser, plan) {
  const frames = path.join(OUT, 'frames');
  fs.mkdirSync(frames, { recursive: true });
  const ctx = await newContext(browser);
  const page = await ctx.newPage(); watchApi(page);
  const cdp = await ctx.newCDPSession(page);
  const index = []; let n = 0; let T = 0; let vt = false; let vtFps = FPS;
  // Frames come from Chromium's own compositor (screencast): on these pages an
  // explicit captureScreenshot re-composites the blurred HUD in software (about
  // 5 s a frame on the Floor) while the compositor's frame costs a fraction.
  // A frame is accepted only if it was produced after the step that should
  // appear in it, and only for the current document (after a navigation the
  // old page's last frame is never reused; a capture is forced instead).
  let latest = null, nav = 0; const waiters = [];
  page.on('framenavigated', fr => { if (fr === page.mainFrame()) { nav++; latest = null; } });
  cdp.on('Page.screencastFrame', f => {
    cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});
    latest = { data: f.data, ts: f.metadata.timestamp, nav, seq: (latest ? latest.seq : 0) + 1 };
    for (let k = waiters.length - 1; k >= 0; k--) if (waiters[k].after <= latest.ts) { waiters[k].resolve(latest); waiters.splice(k, 1); }
  });
  const frameAfter = (after, ms) => new Promise(res => { if (latest && latest.nav === nav && latest.ts >= after) return res(latest);
    const w = { after, resolve: res }; waiters.push(w); setTimeout(() => { const k = waiters.indexOf(w); if (k >= 0) { waiters.splice(k, 1); res(null); } }, ms); });
  const write = async (b64) => { const file = String(n++).padStart(6, '0') + '.jpg'; await fs.promises.writeFile(path.join(frames, file), Buffer.from(b64, 'base64')); return file; };
  const forced = async () => (await cdp.send('Page.captureScreenshot', { format: 'jpeg', quality: 90, optimizeForSpeed: true })).data;
  // a frame for "now": the compositor's newest frame of this document, or a forced capture
  const shotAfter = async (after, ms) => { const f = await frameAfter(after, ms); if (f) return write(f.data);
    if (latest && latest.nav === nav) return write(latest.data); return write(await forced()); };
  const shot = async () => shotAfter(Date.now() / 1000 - 0.05, 400);
  // one stepped frame: optional input, the virtual clock advanced 1/30 s when
  // the page is 3D, then a capture placed exactly 1/30 s after the last
  const stepFrame = async (input) => {
    if (input) await input();
    // a 3D segment may step at 15 fps: each capture then holds two output
    // frames and the virtual clock advances 1/15 s, so timing stays exact
    const dt = vt ? 1 / vtFps : DT;
    const before = Date.now() / 1000;
    const ran = vt ? await page.evaluate(ms => window.__vt ? window.__vt.step(ms) : 0, 1000 * dt).catch(() => 0) : 0;
    // a step that ran the scene's frame callbacks repaints the canvas: wait for
    // that frame; a flat step (cursor / scroll) repaints quickly or not at all
    index.push({ file: await shotAfter(before, ran > 0 ? 8000 : 600), t: T, kind: vt ? 'vt' + vtFps : 'step' }); T += dt;
  };
  // a real-time hold: captures as fast as the page allows, timeline 1:1
  // a real-time hold: every compositor frame of the hold at its own time (1:1)
  const holdReal = async (ms) => {
    const t0 = Date.now(), T0 = T; let seen = -1;
    index.push({ file: await shot(), t: T0, kind: 'real' }); seen = latest ? latest.seq : -1;
    while (Date.now() - t0 < ms) {
      const f = await frameAfter(Date.now() / 1000, Math.max(50, ms - (Date.now() - t0)));
      if (f && f.seq !== seen && f.nav === nav) { seen = f.seq; const tt = T0 + Math.max(0, f.ts - t0 / 1000);
        if (tt < T0 + ms / 1000) index.push({ file: await write(f.data), t: tt, kind: 'real' }); }
    }
    T = T0 + ms / 1000;
  };
  const rate = () => vt ? vtFps : FPS;
  const hold = async (ms) => { if (vt) { for (let k = 0, N = Math.round(ms / 1000 * rate()); k < N; k++) await stepFrame(); } else await holdReal(ms); };
  const glide = async (x, y, ms = 900) => {
    const sx = mouse.x, sy = mouse.y, N = Math.max(4, Math.round(ms / 1000 * rate()));
    for (let k = 1; k <= N; k++) { const q = k / N, e = q < .5 ? 2 * q * q : 1 - Math.pow(-2 * q + 2, 2) / 2;
      await stepFrame(() => page.mouse.move(sx + (x - sx) * e, sy + (y - sy) * e)); }
    mouse = { x, y };
  };
  const wheel = async (dy, ms = 2000) => {
    const N = Math.max(6, Math.round(ms / 1000 * rate())); let done = 0;
    for (let k = 1; k <= N; k++) { const q = k / N, want = Math.round(dy * (q < .5 ? 2 * q * q : 1 - Math.pow(-2 * q + 2, 2) / 2));
      await stepFrame(async () => { if (want !== done) { await page.mouse.wheel(0, want - done); done = want; } await sleep(40); }); }
  };
  const log = [];
  const mark = (i, kind, priv) => {
    log.push({ step: i, video_t: +T.toFixed(2), utc: new Date().toISOString(), kind, detail: priv === undefined ? null : priv });
    console.log(String(T.toFixed(1)).padStart(7), '#' + i, kind);
  };
  const first = plan.find(s => s.goto);
  if (first) { await page.goto(BASE + first.goto, { waitUntil: 'load', timeout: 90000 }).catch(() => {}); await sleep(first.settle || 9000); }
  await page.mouse.move(mouse.x, mouse.y);
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 90, maxWidth: VIEW.width, maxHeight: VIEW.height, everyNthFrame: 1 });
  mark(-1, 'capture_start', { base: BASE });
  for (let i = 0; i < plan.length; i++) {
    const s = plan[i];
    try {
      if (s.chapter) mark(i, 'chapter', s.chapter);
      else if (s.goto) { if (s === first) continue; vt = false; mark(i, 'goto', s.goto); await page.goto(BASE + sub(s.goto), { waitUntil: 'load', timeout: 90000 }); await page.mouse.move(mouse.x, mouse.y); }
      else if (s.vt !== undefined) { vt = !!s.vt; vtFps = s.fps || FPS; await page.evaluate(on => { if (window.__vt) on ? window.__vt.start() : window.__vt.stop(); }, vt); mark(i, vt ? 'vt_on' : 'vt_off');
        if (vt && s.warm) { for (let k = 0; k < s.warm; k++) await page.evaluate(ms => window.__vt.step(ms), 1000 / vtFps); mark(i, 'vt_warm_uncaptured', s.warm); } }
      else if (s.wait) await hold(s.wait);
      else if (s.move) { const p = await locate(page, s.move); await glide(p.x + (s.move.dx || 0), p.y + (s.move.dy || 0), s.ms || 900); }
      else if (s.moveTo) await glide(s.moveTo[0], s.moveTo[1], s.ms || 900);
      else if (s.click) {
        const p = s.click.at ? { x: s.click.at[0], y: s.click.at[1] } : await locate(page, s.click);
        await glide(p.x, p.y, s.ms || 900); await hold(250);
        mark(i, 'click', { target: s.click, resolved: sub(s.click.text || s.click.sel || s.click.name || '') });
        const before = page.url();
        await page.mouse.down(); await sleep(90); await page.mouse.up();
        if (s.click.navigates) { vt = false;
          await page.waitForURL(u => u.href !== before, { timeout: 20000 }).catch(() => mark(i, 'navigation_not_observed'));
          await page.waitForLoadState('load', { timeout: 90000 }).catch(() => {}); await page.mouse.move(mouse.x, mouse.y); }
        if (s.after) await hold(s.after);
      }
      else if (s.scroll) await wheel(s.scroll.dy, s.scroll.ms || 2000);
      else if (s.scrollTo) { const p = await locate(page, Object.assign({ scrollIntoView: false }, s.scrollTo)); const dy = p.y - (s.scrollTo.top || 200); await wheel(dy, s.ms || Math.min(4000, 800 + Math.abs(dy) * 1.5)); }
      else if (s.key) { mark(i, 'key', s.key); await page.keyboard.press(s.key); }
      else if (s.drag) { const [x1, y1, x2, y2] = s.drag; await glide(x1, y1, 700); await page.mouse.down(); await glide(x2, y2, s.ms || 1600); await page.mouse.up(); mark(i, 'drag', s.drag); }
      else if (s.remember) {
        const loc = locator(page, s.remember); await loc.waitFor({ state: 'attached', timeout: s.remember.timeout || 15000 });
        let v = s.remember.attr ? await loc.getAttribute(s.remember.attr) : (await loc.innerText()).trim();
        if (s.remember.regex) { const m = String(v).match(new RegExp(s.remember.regex)); v = m ? (m[1] || m[0]) : null; }
        if (v === null || v === '') throw new Error('nothing to remember');
        VARS[s.remember.name] = v; mark(i, 'remember', { name: s.remember.name, value: v });
      }
      else if (s.snapshot) { const info = await pageInfo(page); fs.mkdirSync(path.join(OUT, 'pages'), { recursive: true });
        fs.writeFileSync(path.join(OUT, 'pages', s.snapshot + '.json'), JSON.stringify(info, null, 1)); mark(i, 'snapshot', s.snapshot); }
      if (s.hold) await hold(s.hold);
    } catch (e) { mark(i, s.optional ? 'skipped' : 'step_failed', { step: s, error: String(e).slice(0, 200) }); }
  }
  mark(plan.length, 'capture_end');
  index.push({ file: await shot(), t: T, kind: 'end' });
  await cdp.send('Page.stopScreencast').catch(() => {});
  fs.writeFileSync(path.join(OUT, 'frames.json'), JSON.stringify(index));
  fs.writeFileSync(path.join(OUT, 'capture_log.json'), JSON.stringify({ base: BASE, viewport: VIEW, fps: FPS, frames: index.length,
    video_seconds: +T.toFixed(2), vars: VARS, stats: STATS, log }, null, 1));
  await ctx.close();
  const fails = log.filter(e => e.kind === 'step_failed').length;
  console.log('frames', index.length, 'video_s', T.toFixed(1), 'failed_steps', fails,
    'api_get', STATS.api_get, 'api_status', JSON.stringify(STATS.api_status), 'non_get_aborted', STATS.non_get_aborted);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined,
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--hide-scrollbars', '--force-color-profile=srgb',
      '--window-size=1920,1080', '--disable-features=Translate,MediaRouter', '--deny-permission-prompts', '--disable-notifications'] });
  try {
    if (MODE === 'discover') await discover(browser);
    else await record(browser, JSON.parse(fs.readFileSync(PLAN, 'utf8')));
  } finally { await browser.close(); }
})();
