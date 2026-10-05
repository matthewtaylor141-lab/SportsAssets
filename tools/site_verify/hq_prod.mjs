// COMMAND HQ 3D · PRODUCTION readback (READ ONLY). Opens the live command host
// signed in (admin token on /api/command/** only, exactly as hq.mjs does), waits
// until /build.json serves EXPECT_SHA, then renders the same nine views as the
// approval preview and records overflow, NaN / undefined / [object Object],
// console errors and every non-GET to /api/** (must be none).
import fs from 'node:fs';
import { chromium } from 'playwright';
import { adrianaReadback } from './adriana_readback.mjs';
const HOST = process.env.HOST || 'https://command.bettortoken.com';
const TOKEN = process.env.ADMIN_TOKEN || '';
const OUT = process.env.OUT || 'site_verify_out';
const EXPECT = process.env.EXPECT_SHA || '';
fs.mkdirSync(OUT, { recursive: true });
const BASE = HOST;
const report = { host: HOST, expect: EXPECT, started: new Date().toISOString(), views: [], build: null };
// wait for the deploy: /build.json must carry the expected SHA (max 15 min)
for (let i = 0; i < 60; i++) {
  try { const r = await fetch(HOST + '/build.json?ts=' + Date.now(), { cache: 'no-store' }); const j = await r.json(); report.build = j; if (!EXPECT || j.sha === EXPECT) break; } catch (e) { report.build = { error: String(e).slice(0, 120) }; }
  await new Promise((r) => setTimeout(r, 15000));
}
console.log('build.json ' + JSON.stringify(report.build));
report.adriana = await adrianaReadback(HOST, TOKEN, OUT);
console.log('adriana ' + JSON.stringify({ seat: report.adriana.seat && { deployed: report.adriana.seat.deployed, state: report.adriana.seat.state, work_state: report.adriana.seat.work_state }, workspace: report.adriana.workspace_http, census: report.adriana.census && { scan: report.adriana.census.scan_id, status: report.adriana.census.status, structures: report.adriana.census.structures_considered, opportunities: report.adriana.census.opportunities, refused: report.adriana.census.refusals_total } }));
const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
async function auth(ctx) { if (TOKEN) await ctx.route(HOST + '/api/command/**', (r) => r.continue({ headers: { ...r.request().headers(), 'x-admin-token': TOKEN } })); }
async function probe(page) {
  return page.evaluate(() => { const t = document.body.innerText; return { sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
    nan: (t.match(/\bNaN\b/g) || []).length, undef: (t.match(/\bundefined\b/g) || []).length, objObj: (t.match(/\[object Object\]/g) || []).length,
    dna: (t.match(/DATA NOT AVAILABLE/g) || []).length, characters: document.body.getAttribute('data-characters'),
    scene: window.__hq && window.__hq.scene ? window.__hq.scene.stats() : null }; });
}
function watchPage(page, sink) {
  page.on('pageerror', (e) => sink.errors.push('PAGEERROR ' + String(e).slice(0, 240)));
  page.on('console', (m) => { if (m.type() === 'error') sink.errors.push('CONSOLE ' + m.text().slice(0, 240)); });
  page.on('request', (q) => { if (q.url().includes('/api/') && q.method() !== 'GET') sink.nonGet.push(q.method() + ' ' + q.url().replace(BASE, '')); });
}

// DESKTOP 1440 x 900: the seven desktop views from one session
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  await ctx.addInitScript(() => { window.__hqFixedQuality = true; });
  await auth(ctx);
  const page = await ctx.newPage();
  const sink = { errors: [], nonGet: [] };
  watchPage(page, sink);
  await page.goto(BASE + '/#command', { waitUntil: 'load', timeout: 90000 });
  await page.waitForFunction(() => document.body.getAttribute('data-characters') != null, null, { timeout: 300000 }).catch(() => sink.errors.push('characters not signalled in 300 s'));
  await page.waitForTimeout(30000);
  const steps = [
    ['1_command_1440', async () => page.evaluate(() => window.__hq.go('command'))],
    ['2_floor_wide_1440', async () => page.evaluate(() => window.__hq.go('floor'))],
    ['3_derek_desk_1440', async () => page.evaluate(() => { window.__hq.go('floor'); window.__hq.openDesk('derek'); })],
    ['4_adriana_desk_1440', async () => page.evaluate(() => { window.__hq.closeDesk(); window.__hq.openDesk('adriana'); })],
    ['5_markets_1440', async () => page.evaluate(() => window.__hq.go('markets'))],
    ['6_capital_risk_1440', async () => page.evaluate(() => window.__hq.go('capital'))],
    ['7_reports_1440', async () => page.evaluate(() => window.__hq.go('reports'))]
  ];
  for (const [name, act] of steps) {
    const before = sink.errors.length;
    try { await act(); } catch (e) { sink.errors.push(name + ' ' + String(e).slice(0, 160)); }
    await page.waitForTimeout(7000);
    await page.evaluate(() => { if (window.__hqScene) window.__hqScene.settle(); }).catch(() => {});
    await page.waitForTimeout(2500);
    const r = await probe(page);
    await page.screenshot({ path: `${OUT}/${name}.png`, timeout: 150000 }).catch((e) => sink.errors.push(name + ' SCREENSHOT ' + String(e).slice(0, 120)));
    report.views.push({ name, ...r, errors: sink.errors.slice(before) });
  }
  report.desktop = { errors: sink.errors.slice(0, 40), nonGet: sink.nonGet };
  await ctx.close();
}
// PHONE 390 x 844: the pocket command center (full page)
for (const [name, hash] of [['8_mobile_command_390', 'command'], ['9_mobile_capital_risk_390', 'capital']]) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1' });
  await auth(ctx);
  const page = await ctx.newPage();
  const sink = { errors: [], nonGet: [] };
  watchPage(page, sink);
  await page.goto(BASE + '/#' + hash, { waitUntil: 'load', timeout: 90000 });
  await page.waitForTimeout(20000);
  const r = await probe(page);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true, timeout: 150000 }).catch((e) => sink.errors.push(name + ' SCREENSHOT ' + String(e).slice(0, 120)));
  report.views.push({ name, ...r, errors: sink.errors, nonGet: sink.nonGet });
  await ctx.close();
}
await browser.close();
report.finished = new Date().toISOString();
fs.writeFileSync(`${OUT}/prod_report.json`, JSON.stringify(report, null, 1));
for (const v of report.views) console.log(`${v.name} overflowX=${v.sw > v.cw} nan=${v.nan} undef=${v.undef} objObj=${v.objObj} dna=${v.dna} characters=${v.characters} errors=${(v.errors || []).length}${v.scene ? ' avatars=' + v.scene.avatars + ' edges=' + v.scene.edges : ''}`);
console.log(`desktop nonGet=${JSON.stringify(report.desktop && report.desktop.nonGet)} errors=${JSON.stringify((report.desktop && report.desktop.errors || []).slice(0, 8))}`);
