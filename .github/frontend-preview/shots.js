// FRONTEND PREVIEW DEVICE MATRIX (read only), for frontend-preview.yml.
//
// Loads Command (/), Floor (/floor) and Trader (/trader) from the preview
// server at desktop, iPhone and iPad sizes (portrait and landscape) plus
// reduced-motion runs, and records per view: HTTP status, sign-in state,
// horizontal overflow, WebGL canvases (is the 3D scene genuinely there and
// how much of the screen it holds), overlapping fixed/sticky bars (a
// full-viewport background layer painted beneath a bar is listed apart, see
// pairBars), touch targets under 44 px on touch devices, PAPER/SHADOW label
// counts, words
// that would betray example/demo data, the current-workspace marker,
// console errors and API statuses. Every non-GET is aborted in the browser
// as well as refused by the server. Screenshots go to <out>/shots/.
'use strict';
const pw = require('playwright');
const H = require('./harness_env');
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
      return true; }).map(el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
        const shown = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
        return { el, r, share: +(shown / (vw * vh)).toFixed(3), z: s.zIndex, pe: s.pointerEvents,
          id: (el.id ? '#' + el.id : el.tagName.toLowerCase()) + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '') }; });
    // >>> BAR PAIRING (pinned by backend/tests/js/frontend-preview-bars.test.cjs)
    // Two fixed / sticky bars that overlap hide each other's content. A
    // FULL-VIEWPORT BACKGROUND LAYER is not a bar: a box showing on >= 90 %
    // of the viewport that paints BENEATH the other box of a pair is the
    // scene the bars float over and cannot cover them. Production device run
    // 37834226533: every Command overlap on desktop / iPad paired #hq-stage
    // (the WebGL headquarters canvas, 100 % of the viewport, z-index 0) or
    // #hq-tags (its desk-label overlay, 100 %, z-index 8, pointer-events
    // none) with a HUD bar at z-index 9-22 above it. Such a pair is not an
    // overlap; the layer is listed in background_layers with every box it
    // sits beneath, so nothing excluded goes unrecorded. A full-viewport box
    // painted ABOVE another box, and any two boxes under 90 %, still overlap.
    const BG_SHARE = 0.9;
    // stacking order: walk each box's chain of stacking contexts from the
    // root; where the chains part, the lower z-index paints beneath (auto =
    // 0) and on a tie the earlier in the document (CSS 2.1 appendix E)
    const formsContext = (el) => { const s = getComputedStyle(el);
      if (s.position === 'fixed' || s.position === 'sticky') return true;
      if (s.zIndex !== 'auto' && (s.position !== 'static' || (el.parentElement && /flex|grid/.test(getComputedStyle(el.parentElement).display)))) return true;
      return Number(s.opacity) < 1 || s.transform !== 'none' || s.filter !== 'none' || (s.backdropFilter || 'none') !== 'none'
        || (s.perspective || 'none') !== 'none' || (s.clipPath || 'none') !== 'none' || (s.maskImage || 'none') !== 'none'
        || s.isolation === 'isolate' || (s.mixBlendMode || 'normal') !== 'normal'
        || /transform|opacity|filter|perspective/.test(s.willChange || '') || /paint|layout|strict|content/.test(s.contain || ''); };
    const contexts = (el) => { const c = []; for (let n = el; n && n !== document.documentElement; n = n.parentElement) if (n === el || formsContext(n)) c.unshift(n); return c; };
    const zOf = (el) => { const z = parseInt(getComputedStyle(el).zIndex, 10); return Number.isFinite(z) ? z : 0; };
    const paintsBeneath = (x, y) => { const cx = contexts(x), cy = contexts(y); let k = 0;
      while (k < cx.length && k < cy.length && cx[k] === cy[k]) k++;
      if (k === cx.length || k === cy.length) return k === cx.length;
      const zx = zOf(cx[k]), zy = zOf(cy[k]); if (zx !== zy) return zx < zy;
      return !!(cx[k].compareDocumentPosition(cy[k]) & Node.DOCUMENT_POSITION_FOLLOWING); };
    // bars: [{id, r, share, z, pe}]; nested(i, j): one box holds the other;
    // beneath(i, j): box i paints beneath box j
    const pairBars = (bars, nested, beneath) => {
      const overlaps = [], layers = {};
      for (let i = 0; i < bars.length; i++) for (let j = i + 1; j < bars.length; j++) {
        if (nested(i, j)) continue;
        const a = bars[i].r, b = bars[j].r;
        const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (!(w > 2 && h > 2)) continue;
        const lo = beneath(i, j) ? i : j, hi = lo === i ? j : i;
        if (bars[lo].share >= BG_SHARE) {
          const L = layers[lo] || (layers[lo] = { id: bars[lo].id, viewport_share: bars[lo].share, z_index: bars[lo].z, pointer_events: bars[lo].pe, beneath: [] });
          L.beneath.push(bars[hi].id);
          continue;
        }
        overlaps.push({ a: bars[i].id, b: bars[j].id, area_px: Math.round(w * h) });
      }
      return { overlaps, background_layers: Object.keys(layers).map(k => layers[k]) };
    };
    // <<< BAR PAIRING
    const paired = pairBars(fixed, (i, j) => fixed[i].el.contains(fixed[j].el) || fixed[j].el.contains(fixed[i].el),
      (i, j) => paintsBeneath(fixed[i].el, fixed[j].el));
    const overlaps = paired.overlaps;
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
      fixed_bars: fixed.length, fixed_overlaps: overlaps.slice(0, 12), background_layers: paired.background_layers,
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
  const launched = await H.launch(pw);
  const browser = launched.browser;
  const records = [];
  for (const [dev, pg, motion] of VIEWS) {
    const d = DEVICES[dev];
    const ctx = await browser.newContext(Object.assign({}, d, { reducedMotion: motion === 'reduce' ? 'reduce' : 'no-preference' }));
    let aborted = 0; const api = {}; const errors = [];
    await ctx.route('**/*', route => {
      const m = route.request().method();
      if (m !== 'GET' && m !== 'HEAD') { aborted++; return route.abort(); }
      return H.continueRead(route, BASE);
    });
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
    page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 200)));
    // a read that never got an answer is counted too, never dropped
    // (with the browser's own reason: a 304 revalidation the page then
    // aborts reads differently from a network error or a timeout)
    page.on('requestfailed', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/api/')) { const f = r.failure(); const k = u.pathname.replace(/\/[0-9a-f-]{8,}/g, '/:id') + ' failed:' + ((f && f.errorText) || '?'); api[k] = (api[k] || 0) + 1; } });
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
  fs.writeFileSync(path.join(OUT, 'preview.json'), JSON.stringify(Object.assign({ base: BASE, at: new Date().toISOString() }, H.describe(BASE, launched), { records }), null, 1));
  for (const r of records) console.log(`${r.view.padEnd(34)} HTTP ${r.status} dcl=${r.dom_content_loaded_ms} fps=${r.fps_4s} lt=${r.long_tasks_4s}/${r.longest_task_ms}ms in=${r.signed_in} ovf=${r.overflow_px} gl=${r.webgl_canvases}/${r.largest_webgl_share} bars=${r.fixed_bars} ovl=${(r.fixed_overlaps || []).length} bg=${(r.background_layers || []).map(l => l.id + ':' + l.beneath.length).join(',') || 0} t<44=${r.touch_targets_under_44} PAPER=${r.paper_mentions} SHADOW=${r.shadow_mentions} cur=${JSON.stringify(r.current_workspace)} err=${r.console_errors} sus=${JSON.stringify(r.suspect_words)}`);
})();
