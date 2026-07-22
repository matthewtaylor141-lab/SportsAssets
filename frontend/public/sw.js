/* Service worker: shows web-push trade alerts and deep-links into the app. */

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = { title: 'SportsAssets Hub', body: event.data && event.data.text() }
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
