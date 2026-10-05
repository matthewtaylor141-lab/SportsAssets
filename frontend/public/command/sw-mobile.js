/* sw-mobile.js — the Mobile Command app shell, and nothing else.
 *
 * Registered from mobile.html with scope './mobile.html', so it controls no
 * other Command page. It answers ONLY for the shell files below (network
 * first, the cached copy when offline). Everything else -- every
 * /api/command/* read, the sign-in POST, the paper ledger stream, /build.json
 * and every other page or asset -- is never intercepted: the browser fetches
 * it exactly as if no worker existed. Live API responses are never cached. */
var VERSION = 'bettor-mobile-shell-v2', PREFIX = 'bettor-mobile-shell-';
var SHELL = ['mobile.html', 'mobile-command.css', 'mobile-command.js', 'unlock.js', 'manifest.webmanifest',
             'brand/bettortoken-app-icon-180.png', 'brand/bettortoken-app-icon-192.png', 'brand/bettortoken-app-icon-32.png'];
var SHELL_URLS = new Set(SHELL.map(function (p) { return new URL(p, self.location.href).href; }));

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(VERSION)
    .then(function (c) { return c.addAll(Array.from(SHELL_URLS).map(function (u) { return new Request(u, {cache: 'reload'}); })); })
    .then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (e) {
  // only this worker's own older caches; never another worker's (the React app's sw.js)
  e.waitUntil(caches.keys()
    .then(function (ks) { return Promise.all(ks.filter(function (k) { return k.indexOf(PREFIX) === 0 && k !== VERSION; }).map(function (k) { return caches.delete(k); })); })
    .then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') { return; }
  var u = new URL(req.url), key = u.origin + u.pathname;
  if (!SHELL_URLS.has(key)) { return; }             // not a shell file: the browser's own fetch
  e.respondWith(fetch(req).then(function (res) {
    if (res.ok && res.type === 'basic' && !res.redirected) {
      var copy = res.clone();
      caches.open(VERSION).then(function (c) { return c.put(key, copy); }).catch(function () {});
    }
    return res;
  }, function () {
    return caches.open(VERSION).then(function (c) { return c.match(key); }).then(function (hit) { return hit || Response.error(); });
  }));
});
