// COMMAND HQ 3D · owner-approval preview (READ ONLY; nothing is deployed).
//
// Serves the candidate frontend (CAND/frontend/public/command, branch
// claude/ui-command-v2) from this runner on 127.0.0.1 and renders it signed in
// against the LIVE production read API: every GET under /api/command/ is
// forwarded to HOST with the admin token (the same header hq.mjs sends);
// any other method is refused here with 405 and never leaves the runner.
// Writes the nine approval screenshots and preview_report.json (per view:
// overflow, NaN / undefined / [object Object], console errors, every non-GET
// the page attempted -- must be none) to OUT.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const HOST = process.env.HOST || 'https://command.bettortoken.com';
const TOKEN = process.env.ADMIN_TOKEN || '';
const OUT = process.env.OUT || 'site_verify_out';
const CAND = process.env.CAND;
const ROOT = path.join(CAND, 'frontend', 'public', 'command');
const PORT = 8899;
fs.mkdirSync(OUT, { recursive: true });
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript', '.css': 'text/css; charset=utf-8', '.json': 'application/json',
  '.glb': 'model/gltf-binary', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.txt': 'text/plain; charset=utf-8', '.md': 'text/plain; charset=utf-8' };
const proxied = { get: 0, refused: [], status: {} };

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://127.0.0.1');
  if (u.pathname.startsWith('/api/')) {
    if (req.method !== 'GET' && req.method !== 'HEAD') { proxied.refused.push(req.method + ' ' + u.pathname); res.writeHead(405, { 'content-type': 'application/json' }); res.end('{"error":"read-only preview: ' + req.method + ' refused"}'); return; }
    if (!u.pathname.startsWith('/api/command/')) { res.writeHead(404); res.end('{}'); return; }
    try {
      const r = await fetch(HOST + u.pathname + u.search, { method: 'GET', headers: { 'x-admin-token': TOKEN, accept: 'application/json' }, signal: AbortSignal.timeout(30000) });
      const body = Buffer.from(await r.arrayBuffer());
      proxied.get++; proxied.status[u.pathname] = r.status;
      res.writeHead(r.status, { 'content-type': r.headers.get('content-type') || 'application/json', 'cache-control': 'no-store' }); res.end(body);
    } catch (e) { res.writeHead(502, { 'content-type': 'application/json' }); res.end(JSON.stringify({ detail: { reason: 'PREVIEW_PROXY: ' + String(e).slice(0, 120) } })); }
    return;
  }
  let p = decodeURIComponent(u.pathname);
  if (p === '/' || p === '') p = '/index.html';
  const file = path.join(ROOT, p);
  if (!file.startsWith(ROOT)) { res.writeHead(403); res.end(); return; }
  fs.readFile(file, (err, buf) => {
    if (err) { res.writeHead(404); res.end('not found'); return; }
    res.writeHead(200, { 'content-type': TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream' }); res.end(buf);
  });
});
await new Promise((r) => server.listen(PORT, '127.0.0.1', r));
const BASE = 'http://127.0.0.1:' + PORT;

const report = { host: HOST, candidate: process.env.CAND_SHA || null, started: new Date().toISOString(), views: [], proxied };
const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });

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
    ['4_ariana_desk_1440', async () => page.evaluate(() => { window.__hq.closeDesk(); window.__hq.openDesk('ariana'); })],
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
    await page.screenshot({ path: `${OUT}/${name}.png` });
    report.views.push({ name, ...r, errors: sink.errors.slice(before) });
  }
  report.desktop = { errors: sink.errors.slice(0, 40), nonGet: sink.nonGet };
  await ctx.close();
}
// PHONE 390 x 844: the pocket command center (full page)
for (const [name, hash] of [['8_mobile_command_390', 'command'], ['9_mobile_capital_risk_390', 'capital']]) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1' });
  const page = await ctx.newPage();
  const sink = { errors: [], nonGet: [] };
  watchPage(page, sink);
  await page.goto(BASE + '/#' + hash, { waitUntil: 'load', timeout: 90000 });
  await page.waitForTimeout(20000);
  const r = await probe(page);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  report.views.push({ name, ...r, errors: sink.errors, nonGet: sink.nonGet });
  await ctx.close();
}
await browser.close();
server.close();
report.finished = new Date().toISOString();
fs.writeFileSync(`${OUT}/preview_report.json`, JSON.stringify(report, null, 1));
for (const v of report.views) console.log(`${v.name} overflowX=${v.sw > v.cw} nan=${v.nan} undef=${v.undef} objObj=${v.objObj} dna=${v.dna} characters=${v.characters} errors=${(v.errors || []).length}${v.scene ? ' avatars=' + v.scene.avatars + ' edges=' + v.scene.edges : ''}`);
console.log(`desktop nonGet=${JSON.stringify(report.desktop && report.desktop.nonGet)} errors=${JSON.stringify((report.desktop && report.desktop.errors || []).slice(0, 8))}`);
console.log(`proxied GETs=${proxied.get} refused=${JSON.stringify(proxied.refused)} status=${JSON.stringify(proxied.status)}`);
