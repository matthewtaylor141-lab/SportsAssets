// FRONTEND PREVIEW SERVER (read only), for frontend-preview.yml.
//
// Serves a built frontend (dist/) on 127.0.0.1 as the COMMAND host would:
// the target ref's own netlify.toml [[redirects]] are applied in order, the
// command.bettortoken.com host rules included (the host prefix is the
// preview's own host). /api/command/* is proxied to the API exactly as the
// Netlify rule does, with two differences that keep the preview read only:
//   * only GET and HEAD are forwarded; every other method is refused here
//     (405) and never reaches the API;
//   * the read-role X-Admin-Token is added HERE, server side. The browser
//     never sees it: it is not in the page, a URL, a cookie or a response,
//     and Set-Cookie from the API is dropped.
// Every proxied call is counted by method/status and written to
// <out>/proxy.json on exit (SIGTERM) and on GET /__preview/stats.
'use strict';
const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');

const [DIST_ARG, TOML, PORT, OUT] = process.argv.slice(2);
const DIST = path.resolve(DIST_ARG);
const TOKEN = process.env.ADMIN_TOKEN || '';
const HOST = 'https://command.bettortoken.com';
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
  '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.webp': 'image/webp',
  '.woff2': 'font/woff2', '.woff': 'font/woff', '.glb': 'model/gltf-binary',
  '.gltf': 'model/gltf+json', '.webmanifest': 'application/manifest+json',
  '.txt': 'text/plain' };

function rules(toml) {
  const out = [];
  for (const block of fs.readFileSync(toml, 'utf8').split('[[redirects]]').slice(1)) {
    const body = block.split(/\n\[/)[0];
    const get = k => { const m = body.match(new RegExp('^\\s*' + k + '\\s*=\\s*"([^"]*)"', 'm')); return m ? m[1] : null; };
    const from = get('from'); const to = get('to');
    if (from && to) out.push({ from: from.startsWith(HOST) ? from.slice(HOST.length) || '/' : from, to });
  }
  return out;
}
const RULES = rules(TOML);

function match(pattern, p) {
  if (pattern.endsWith('/*')) {
    const base = pattern.slice(0, -1);
    if (p.startsWith(base)) return { splat: p.slice(base.length) };
    if (p + '/' === base) return { splat: '' };
    return null;
  }
  return pattern === p ? { splat: '' } : null;
}

const stats = { started_at: new Date().toISOString(), token_supplied: !!TOKEN,
                rewrites: 0, static_404: 0, api: {}, refused_non_get: 0 };
function dump() {
  if (!OUT) return;
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(path.join(OUT, 'proxy.json'), JSON.stringify(stats, null, 1));
}

function serveFile(res, rel) {
  let file = path.join(DIST, decodeURIComponent(rel));
  if (file !== DIST && !file.startsWith(DIST + path.sep)) { res.writeHead(403); return res.end(); }
  try { if (fs.statSync(file).isDirectory()) file = path.join(file, 'index.html'); } catch (e) { /* 404 below */ }
  fs.readFile(file, (err, buf) => {
    if (err) { stats.static_404++; res.writeHead(404, { 'content-type': 'text/plain' }); return res.end('not found'); }
    res.writeHead(200, { 'content-type': TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream',
                         'cache-control': 'no-store' });
    res.end(buf);
  });
}

function proxy(req, res, target) {
  const key = (k) => { stats.api[k] = (stats.api[k] || 0) + 1; };
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    stats.refused_non_get++; key(req.method + ' 405-refused-by-preview');
    res.writeHead(405, { 'content-type': 'application/json' });
    return res.end(JSON.stringify({ refused: 'PREVIEW_IS_READ_ONLY', method: req.method }));
  }
  const u = new URL(target);
  const headers = { accept: req.headers.accept || 'application/json',
                    'user-agent': 'bettor-frontend-preview' };
  if (TOKEN) headers['x-admin-token'] = TOKEN;
  const up = https.request({ method: req.method, hostname: u.hostname, path: u.pathname + u.search,
                             headers, timeout: 90000 }, (r) => {
    key(req.method + ' ' + r.statusCode);
    const h = Object.assign({}, r.headers);
    delete h['set-cookie'];
    res.writeHead(r.statusCode, h);
    r.pipe(res);
  });
  up.on('timeout', () => up.destroy(new Error('timeout')));
  up.on('error', (e) => { key(req.method + ' upstream-error'); if (!res.headersSent) res.writeHead(502); res.end(String(e.message || e)); });
  up.end();
}

http.createServer((req, res) => {
  const u = new URL(req.url, 'http://preview.local');
  if (u.pathname === '/__preview/stats') { dump(); res.writeHead(200, { 'content-type': 'application/json' }); return res.end(JSON.stringify(stats)); }
  for (const r of RULES) {
    const m = match(r.from, u.pathname);
    if (!m) continue;
    const to = r.to.replace(':splat', m.splat);
    if (/^https?:\/\//.test(to)) return proxy(req, res, to + u.search);
    stats.rewrites++;
    return serveFile(res, to);
  }
  return serveFile(res, u.pathname);
}).listen(Number(PORT), '127.0.0.1', () => console.log('preview on 127.0.0.1:' + PORT + ' rules=' + RULES.length));

process.on('SIGTERM', () => { dump(); process.exit(0); });
process.on('SIGINT', () => { dump(); process.exit(0); });
