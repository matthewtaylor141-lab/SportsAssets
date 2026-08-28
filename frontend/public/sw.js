/* Service worker: shows web-push trade alerts and deep-links into the app,
 * and keeps a small app-shell cache so standalone (Add to Home Screen)
 * launches paint instantly even on a bad connection.
 *
 * Caching contract:
 *  - /api/* is NEVER cached — every number on the site stays live.
 *  - Navigations are network-first (fresh HTML/new builds win) with a
 *    short timeout falling back to the cached shell, so a flaky or
 *    offline launch still opens the app.
 *  - Hashed build assets (/assets/*) are cache-first: immutable by name.
 *  - Only same-origin GET requests are ever cached.
 */

const SHELL_CACHE = 'bt-shell-v2'
const ASSET_CACHE = 'bt-assets-v2'
const NAV_TIMEOUT_MS = 3000
const ASSET_CACHE_MAX = 80

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((c) =>
        c.addAll(['/', '/manifest.webmanifest', '/favicon.svg', '/icon-192.png', '/icon-512.png']),
      )
      .catch(() => {}) // precache is best-effort; never block install
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k !== SHELL_CACHE && k !== ASSET_CACHE)
            .map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  )
})

/** Keep the runtime asset cache from growing without bound across deploys. */
async function trimAssetCache() {
  const cache = await caches.open(ASSET_CACHE)
  const keys = await cache.keys()
  for (let i = 0; i < keys.length - ASSET_CACHE_MAX; i++) await cache.delete(keys[i])
}

async function handleNavigation(request) {
  const cache = await caches.open(SHELL_CACHE)
  try {
    const fresh = await Promise.race([
      fetch(request),
      new Promise((_, reject) => setTimeout(() => reject(new Error('nav-timeout')), NAV_TIMEOUT_MS)),
    ])
    if (fresh && fresh.ok) cache.put('/', fresh.clone())
    return fresh
  } catch {
    const cached = await cache.match('/')
    if (cached) return cached
    return fetch(request) // no cache to fall back on: let the network error surface
  }
}

async function handleAsset(request) {
  const cache = await caches.open(ASSET_CACHE)
  const cached = await cache.match(request)
  if (cached) return cached
  const fresh = await fetch(request)
  // Netlify's SPA fallback answers 200 text/html for files that no longer
  // exist (e.g. a stale hashed bundle after a deploy) — never cache that
  // as an asset, or the app would replay HTML where JS was expected.
  const type = (fresh && fresh.headers.get('content-type')) || ''
  if (fresh && fresh.ok && !type.includes('text/html')) {
    cache.put(request, fresh.clone())
    trimAssetCache()
  }
  return fresh
}

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.origin !== self.location.origin) return // fonts etc: straight through
  if (url.pathname.startsWith('/api/')) return // live data is never cached
  if (req.mode === 'navigate') {
    event.respondWith(handleNavigation(req))
    return
  }
  if (url.pathname.startsWith('/assets/') || /\.(png|svg|woff2?)$/.test(url.pathname)) {
    event.respondWith(handleAsset(req))
  }
})

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = { title: 'BettorToken', body: event.data && event.data.text() }
  }
  event.waitUntil(
    self.registration.showNotification(data.title || 'Whale trade', {
      body: data.body || '',
      tag: data.kind === 'summary' ? 'burst-summary' : undefined,
      data: { url: data.url || '/' },
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const url = (event.notification.data && event.notification.data.url) || '/'
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if ('focus' in w) {
          w.navigate(url)
          return w.focus()
        }
      }
      return clients.openWindow(url)
    }),
  )
})
