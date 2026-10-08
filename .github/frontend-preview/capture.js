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
//   record    one tab runs a plan of real interactions while Chromium's own
//             compositor frames are captured over CDP (Page.startScreencast,
//             JPEG, 1920x1080) with their timestamps; assemble.py turns them
//             into constant-30 fps H.264. Clicks are real mouse input at the
//             element after a visible, time-bounded cursor glide; scrolling is
//             real wheel input. A small cursor is drawn because headless
//             Chromium paints none.
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
const ROUTES = ['/', '/floor', '/scout', '/derek', '/karen', '/allocator', '/archer', '/xavier',
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
  await loc.waitFor({ state: 'visible', timeout: t.timeout || 12000 });
  if (t.scrollIntoView !== false) await loc.scrollIntoViewIfNeeded({ timeout: 8000 });
  const b = await loc.boundingBox({ timeout: 8000 });
  if (!b) throw new Error('target not visible');
  return { x: b.x + Math.min(b.width / 2, t.xMax || 1e9), y: b.y + Math.min(b.height / 2, 18) };
}

async function glide(page, x, y, ms = 900) {
  // time-bounded: on a busy page each input event waits for the main thread
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
  const page = await ctx.newPage(); watchApi(page);
  const cdp = await ctx.newCDPSession(page);
  const index = []; let n = 0; let pending = Promise.resolve();
  cdp.on('Page.screencastFrame', f => {
    const i = n++; const file = String(i).padStart(6, '0') + '.jpg';
    pending = pending.then(() => fs.promises.writeFile(path.join(frames, file), Buffer.from(f.data, 'base64')));
    index.push({ file, t: f.metadata.timestamp, w: f.metadata.deviceWidth, h: f.metadata.deviceHeight });
    cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});
  });
  const log = []; let tStart = null;
  // public: step number and kind only; private (encrypted log): resolved detail
  const mark = (i, kind, priv) => {
    const t = tStart === null ? 0 : (Date.now() - tStart) / 1000;
    log.push({ step: i, t_s: +t.toFixed(2), utc: new Date().toISOString(), kind, detail: priv === undefined ? null : priv });
    console.log(String(t.toFixed(1)).padStart(7), '#' + i, kind);
  };
  const first = plan.find(s => s.goto);
  if (first) { await page.goto(BASE + first.goto, { waitUntil: 'load', timeout: 60000 }).catch(() => {}); await sleep(first.settle || 9000); }
  await page.mouse.move(mouse.x, mouse.y);
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 90, maxWidth: VIEW.width, maxHeight: VIEW.height, everyNthFrame: 1 });
  tStart = Date.now();
  mark(-1, 'capture_start', { base: BASE });
  for (let i = 0; i < plan.length; i++) {
    const s = plan[i];
    try {
      if (s.chapter) mark(i, 'chapter', s.chapter);
      else if (s.goto) { if (s === first) continue; mark(i, 'goto', s.goto); await page.goto(BASE + sub(s.goto), { waitUntil: 'load', timeout: 60000 }); await page.mouse.move(mouse.x, mouse.y); }
      else if (s.wait) await sleep(s.wait);
      else if (s.move) { const p = await locate(page, s.move); await glide(page, p.x + (s.move.dx || 0), p.y + (s.move.dy || 0), s.ms || 900); }
      else if (s.moveTo) await glide(page, s.moveTo[0], s.moveTo[1], s.ms || 900);
      else if (s.click) {
        const p = s.click.at ? { x: s.click.at[0], y: s.click.at[1] } : await locate(page, s.click);
        await glide(page, p.x, p.y, s.ms || 900); await sleep(300);
        mark(i, 'click', { target: s.click, resolved: sub(s.click.text || s.click.sel || s.click.name || '') });
        await page.mouse.down(); await sleep(90); await page.mouse.up();
        if (s.after) await sleep(s.after);
      }
      else if (s.scroll) await wheel(page, s.scroll.dy, s.scroll.ms || 2000);
      else if (s.scrollTo) { const p = await locate(page, Object.assign({ scrollIntoView: false }, s.scrollTo)); const dy = p.y - (s.scrollTo.top || 200); await wheel(page, dy, s.ms || Math.min(4000, 800 + Math.abs(dy) * 1.5)); }
      else if (s.key) { mark(i, 'key', s.key); await page.keyboard.press(s.key); }
      else if (s.drag) { const [x1, y1, x2, y2] = s.drag; await glide(page, x1, y1, 700); await page.mouse.down(); await glide(page, x2, y2, s.ms || 1600); await page.mouse.up(); mark(i, 'drag', s.drag); }
      else if (s.remember) {
        const loc = locator(page, s.remember); await loc.waitFor({ state: 'attached', timeout: s.remember.timeout || 12000 });
        let v = s.remember.attr ? await loc.getAttribute(s.remember.attr) : (await loc.innerText()).trim();
        if (s.remember.regex) { const m = String(v).match(new RegExp(s.remember.regex)); v = m ? (m[1] || m[0]) : null; }
        if (v === null || v === '') throw new Error('nothing to remember');
        VARS[s.remember.name] = v; mark(i, 'remember', { name: s.remember.name, value: v });
      }
      else if (s.snapshot) { const info = await pageInfo(page); fs.mkdirSync(path.join(OUT, 'pages'), { recursive: true });
        fs.writeFileSync(path.join(OUT, 'pages', s.snapshot + '.json'), JSON.stringify(info, null, 1)); mark(i, 'snapshot', s.snapshot); }
      if (s.hold) await sleep(s.hold);
    } catch (e) { mark(i, s.optional ? 'skipped' : 'step_failed', { step: s, error: String(e).slice(0, 200) }); }
  }
  mark(plan.length, 'capture_end');
  await sleep(800);
  await cdp.send('Page.stopScreencast').catch(() => {});
  await sleep(500); await pending;
  fs.writeFileSync(path.join(OUT, 'frames.json'), JSON.stringify(index));
  fs.writeFileSync(path.join(OUT, 'capture_log.json'), JSON.stringify({ base: BASE, viewport: VIEW, frames: index.length,
    frame_sizes: [...new Set(index.map(f => f.w + 'x' + f.h))], vars: VARS, stats: STATS, log }, null, 1));
  await ctx.close();
  const fails = log.filter(e => e.kind === 'step_failed').length;
  console.log('frames', index.length, 'sizes', [...new Set(index.map(f => f.w + 'x' + f.h))].join(','), 'failed_steps', fails,
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
