// FRONTEND / iPad / MOBILE EVIDENCE for pm-acceptance (read only).
// Loads the COMMAND site at phone, iPad (portrait + landscape) and desktop
// viewports. The page's own /api/command/* GETs are given the read-role
// X-Admin-Token in flight (Playwright request routing; never typed into the
// page, never in a URL, never written to disk); every non-GET is aborted, so
// the check cannot write anything. Records, per device: HTTP status, whether
// the sign-in panel is still shown, horizontal overflow, API responses by
// status, console errors, and a screenshot under acc/viewports/.
const { chromium } = require('playwright');
const fs = require('fs');
const BASE = process.env.COMMAND_BASE || 'https://command.bettortoken.com';
const TOKEN = process.env.ADMIN_TOKEN || '';
const DEVICES = [
  { name: 'iphone_390x844', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 },
  { name: 'ipad_portrait_820x1180', viewport: { width: 820, height: 1180 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
  { name: 'ipad_landscape_1180x820', viewport: { width: 1180, height: 820 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
  { name: 'desktop_1440x900', viewport: { width: 1440, height: 900 }, isMobile: false, hasTouch: false, deviceScaleFactor: 1 },
];
(async () => {
  fs.mkdirSync('acc/viewports', { recursive: true });
  const browser = await chromium.launch();
  const out = [];
  for (const d of DEVICES) {
    const ctx = await browser.newContext({ viewport: d.viewport, isMobile: d.isMobile, hasTouch: d.hasTouch, deviceScaleFactor: d.deviceScaleFactor });
    const api = {};
    let aborted = 0;
    await ctx.route('**/api/**', route => {
      const req = route.request();
      if (req.method() !== 'GET' && req.method() !== 'HEAD') { aborted++; return route.abort(); }
      return route.continue({ headers: Object.assign({}, req.headers(), TOKEN ? { 'x-admin-token': TOKEN } : {}) });
    });
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 160)); });
    page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 160)));
    page.on('response', r => { if (r.url().includes('/api/')) { const k = String(r.status()); api[k] = (api[k] || 0) + 1; } });
    let status = null;
    try { const r = await page.goto(BASE + '/', { waitUntil: 'networkidle', timeout: 60000 }); status = r ? r.status() : null; }
    catch (e) { errors.push('goto: ' + String(e).slice(0, 160)); }
    await page.waitForTimeout(4000);
    if (status === null) {   // a navigation served without a response object
      status = await page.evaluate(() => fetch(location.href, { method: 'HEAD', cache: 'no-store' }).then(r => r.status)).catch(() => null);
    }
    const m = await page.evaluate(() => ({
      title: document.title,
      scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth,
      unlockShown: !!document.getElementById('command-unlock'),
      textChars: (document.body && document.body.innerText || '').length,
    })).catch(() => ({}));
    const shot = 'acc/viewports/' + d.name + '.png';
    await page.screenshot({ path: shot }).catch(() => {});
    out.push({ device: d.name, viewport: d.viewport, status, title: m.title,
               signed_in: m.unlockShown === false, text_chars: m.textChars,
               horizontal_overflow_px: (m.scrollWidth || 0) - (m.innerWidth || 0),
               no_horizontal_scroll: (m.scrollWidth || 0) <= (m.innerWidth || 0),
               api_responses_by_status: api, non_get_aborted: aborted,
               console_errors: errors.length, first_errors: errors.slice(0, 5), screenshot: shot });
    await ctx.close();
  }
  await browser.close();
  const ok = out.every(r => r.status === 200 && r.signed_in && r.no_horizontal_scroll);
  fs.writeFileSync('acc/frontend_viewports.json', JSON.stringify({ base: BASE, at: new Date().toISOString(), token_supplied: !!TOKEN, all_devices_ok: ok, records: out }, null, 1));
  for (const r of out) console.log(`${r.device.padEnd(24)} HTTP ${r.status} signed_in=${r.signed_in} overflow=${r.horizontal_overflow_px}px api=${JSON.stringify(r.api_responses_by_status)} errors=${r.console_errors}`);
  console.log('all_devices_ok=' + ok);
})();
