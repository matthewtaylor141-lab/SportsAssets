// FRONTEND PREVIEW DEVICE MATRIX (read only), for frontend-preview.yml.
//
// Loads Command (/), Floor (/floor) and Trader (/trader) from the preview
// server at desktop, iPhone and iPad sizes (portrait and landscape) plus
// reduced-motion runs, and records per view: HTTP status, sign-in state,
// horizontal overflow, WebGL canvases (is the 3D scene genuinely there and
// how much of the screen it holds), overlapping fixed/sticky bars, touch
// targets under 44 px on touch devices, PAPER/SHADOW label counts, words
// that would betray example/demo data, the current-workspace marker,
// console errors and API statuses. Every non-GET is aborted in the browser
// as well as refused by the server. Screenshots go to <out>/shots/.
'use strict';
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.argv[2];
const OUT = process.argv[3];
const PHONE = { isMobile: true, hasTouch: true };
const DEVICES = {
  desktop: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false },
  iphone: Object.assign({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3 }, PHONE),
  iphone_landscape: Object.assign({ viewport: { width: 844, height: 390 }, deviceScaleFactor: 3 }, PHONE),
  ipad_portrait: Object.assign({ viewport: { width: 820, height: 1180 }, deviceScaleFactor: 2 }, PHONE),
  ipad_landscape: Object.assign({ viewport: { width: 1180, height: 820 }, deviceScaleFactor: 2 }, PHONE),
};
const PAGES = { command: '/', floor: '/floor', trader: '/trader' };
// the nine approval views first, then the extra states
const VIEWS = [
  ['desktop', 'command'], ['desktop', 'floor'], ['desktop', 'trader'],
  ['iphone', 'command'], ['iphone', 'floor'], ['iphone', 'trader'],
  ['ipad_portrait', 'floor'], ['ipad_portrait', 'trader'], ['ipad_landscape', 'floor'],
  ['ipad_portrait', 'command'], ['ipad_landscape', 'trader'], ['ipad_landscape', 'command'],
  ['iphone_landscape', 'floor'], ['iphone_landscape', 'trader'],
  ['iphone', 'floor', 'reduce'], ['desktop', 'trader', 'reduce'], ['iphone', 'command', 'reduce'],
];
const SUSPECT = /\b(lorem|ipsum|example data|sample data|demo data|placeholder|mock data|fake)\b/i;

async function measure(page, touch) {
  return page.evaluate(({ touch, suspectSrc }) => {
    const vis = (el) => { const s = getComputedStyle(el); const r = el.getBoundingClientRect();
      return s.visibility !== 'hidden' && s.display !== 'none' && Number(s.opacity) > 0.05 && r.width > 0 && r.height > 0; };
    const vw = innerWidth, vh = innerHeight;
    const canvases = [...document.querySelectorAll('canvas')].filter(vis).map(c => {
      // three.js stamps its WebGL canvases; probing getContext() would create one
      const gl = c.getAttribute('data-engine') || null;
      const r = c.getBoundingClientRect();
      const shown = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
      return { gl, w: Math.round(r.width), h: Math.round(r.height), viewport_share: +(shown / (vw * vh)).toFixed(3) };
    });
    const fixed = [...document.querySelectorAll('body *')].filter(el => {
      const p = getComputedStyle(el).position; if (p !== 'fixed' && p !== 'sticky') return false;
      if (!vis(el)) return false;
      let a = el.parentElement; while (a && a !== document.body) { const q = getComputedStyle(a).position; if (q === 'fixed' || q === 'sticky') return false; a = a.parentElement; }
      return true; }).map(el => ({ el, r: el.getBoundingClientRect(),
        id: (el.id ? '#' + el.id : el.tagName.toLowerCase()) + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '') }));
    const overlaps = [];
    for (let i = 0; i < fixed.length; i++) for (let j = i + 1; j < fixed.length; j++) {
      const a = fixed[i], b = fixed[j];
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const w = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
      const h = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
      if (w > 2 && h > 2) overlaps.push({ a: a.id, b: b.id, area_px: Math.round(w * h) });
    }
    const small = [];
    if (touch) for (const el of document.querySelectorAll('a[href],button,[role=button],[role=tab],input,select,summary,[tabindex]:not([tabindex="-1"])')) {
      if (!vis(el)) continue; const r = el.getBoundingClientRect();
      if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) continue;
      if (r.width < 44 || r.height < 44) small.push({ t: (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 40), w: Math.round(r.width), h: Math.round(r.height) });
    }
    const text = (document.body && document.body.innerText) || '';
    const cur = [...document.querySelectorAll('[aria-current]')].filter(vis).map(e => (e.textContent || '').trim().slice(0, 30));
    const suspect = text.match(new RegExp(suspectSrc, 'ig')) || [];
    return {
      title: document.title, signed_in: !document.getElementById('command-unlock'),
      overflow_px: document.documentElement.scrollWidth - innerWidth,
      reduced_motion: matchMedia('(prefers-reduced-motion: reduce)').matches,
      canvases, webgl_canvases: canvases.filter(c => c.gl).length,
      largest_webgl_share: Math.max(0, ...canvases.filter(c => c.gl).map(c => c.viewport_share)),
      fixed_bars: fixed.length, fixed_overlaps: overlaps.slice(0, 12),
      touch_targets_under_44: small.length, touch_targets_under_44_first: small.slice(0, 12),
      paper_mentions: (text.match(/\bPAPER\b/g) || []).length, shadow_mentions: (text.match(/\bSHADOW\b/g) || []).length,
      current_workspace: cur.slice(0, 4), suspect_words: [...new Set(suspect.map(s => s.toLowerCase()))],
      text_chars: text.length,
      safe_area_css: [...document.styleSheets].some(s => { try { return [...s.cssRules].some(r => /safe-area-inset/.test(r.cssText)); } catch (e) { return false; } }),
    };
  }, { touch, suspectSrc: SUSPECT.source });
}

(async () => {
  fs.mkdirSync(path.join(OUT, 'shots'), { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined, args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const records = [];
  for (const [dev, pg, motion] of VIEWS) {
    const d = DEVICES[dev];
    const ctx = await browser.newContext(Object.assign({}, d, { reducedMotion: motion === 'reduce' ? 'reduce' : 'no-preference' }));
    let aborted = 0; const api = {}; const errors = [];
    await ctx.route('**/*', route => {
      const m = route.request().method();
      if (m !== 'GET' && m !== 'HEAD') { aborted++; return route.abort(); }
      return route.continue();
    });
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
    page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 200)));
    page.on('response', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/api/')) { const k = u.pathname.replace(/\/[0-9a-f-]{8,}/g, '/:id') + ' ' + r.status(); api[k] = (api[k] || 0) + 1; } });
    let status = null;
    // COMMAND polls its feeds, so 'networkidle' may never come: wait for the
    // load event, then a fixed settle for the first reads and the 3D scene
    try { const r = await page.goto(BASE + PAGES[pg], { waitUntil: 'load', timeout: 60000 }); status = r ? r.status() : null; }
    catch (e) { errors.push('goto: ' + String(e).slice(0, 200)); }
    await page.waitForTimeout(9000);
    const name = `${dev}_${pg}${motion ? '_reduced_motion' : ''}`;
    // a page whose main thread never yields (e.g. a self-feeding observer)
    // never reaches DOMContentLoaded and cannot answer a 5 s evaluate
    const ready = await Promise.race([new Promise((_, rej) => setTimeout(() => rej(new Error('main thread did not answer in 20 s')), 20000)), page.evaluate(() => { const n = performance.getEntriesByType('navigation')[0];
      return { dom_content_loaded_ms: n && n.domContentLoadedEventEnd ? Math.round(n.domContentLoadedEventEnd) : null,
               load_ms: n && n.loadEventEnd ? Math.round(n.loadEventEnd) : null, ready_state: document.readyState }; })])
      .then(r => Object.assign({ main_thread_responsive: true }, r))
      .catch(e => ({ main_thread_responsive: false, ready_error: String(e).slice(0, 120) }));
    // rendering cost: frames painted and main-thread long tasks over 4 s
    const perf = ready.main_thread_responsive ? await Promise.race([
      new Promise(res => setTimeout(() => res({ perf_error: 'no answer in 30 s' }), 30000)),
      page.evaluate(() => new Promise(res => {
        let frames = 0, long = 0, longest = 0; const t0 = performance.now();
        let po = null;
        try { po = new PerformanceObserver(l => { for (const e of l.getEntries()) { long++; longest = Math.max(longest, e.duration); } }); po.observe({ type: 'longtask', buffered: false }); } catch (e) { po = null; }
        const tick = () => { frames++; if (performance.now() - t0 < 4000) requestAnimationFrame(tick); else { if (po) po.disconnect(); res({ fps_4s: +(frames / ((performance.now() - t0) / 1000)).toFixed(1), long_tasks_4s: long, longest_task_ms: Math.round(longest) }); } };
        requestAnimationFrame(tick);
      }))]).catch(e => ({ perf_error: String(e).slice(0, 120) })) : {};
    const m = ready.main_thread_responsive ? await measure(page, !!d.hasTouch).catch(e => ({ measure_error: String(e).slice(0, 200) })) : {};
    await page.screenshot({ path: path.join(OUT, 'shots', name + '.png'), timeout: 20000 }).catch(() => {});
    if (ready.main_thread_responsive) await page.screenshot({ path: path.join(OUT, 'shots', name + '_full.png'), fullPage: true, timeout: 20000 }).catch(() => {});
    records.push(Object.assign({ view: name, device: dev, page: pg, viewport: d.viewport, status, non_get_aborted: aborted,
      console_errors: errors.length, first_errors: errors.slice(0, 6), api_by_path_status: api }, ready, perf, m));
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, 'preview.json'), JSON.stringify({ base: BASE, at: new Date().toISOString(), records }, null, 1));
  for (const r of records) console.log(`${r.view.padEnd(34)} HTTP ${r.status} dcl=${r.dom_content_loaded_ms} fps=${r.fps_4s} lt=${r.long_tasks_4s}/${r.longest_task_ms}ms in=${r.signed_in} ovf=${r.overflow_px} gl=${r.webgl_canvases}/${r.largest_webgl_share} bars=${r.fixed_bars} ovl=${(r.fixed_overlaps || []).length} t<44=${r.touch_targets_under_44} PAPER=${r.paper_mentions} SHADOW=${r.shadow_mentions} cur=${JSON.stringify(r.current_workspace)} err=${r.console_errors} sus=${JSON.stringify(r.suspect_words)}`);
})();
