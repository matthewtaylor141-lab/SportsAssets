import { api, userKey } from './api'

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = '='.repeat((4 - (base64.length % 4)) % 4)
  const raw = atob((base64 + padding).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)))
}

export async function pushSupported(): Promise<boolean> {
  return 'serviceWorker' in navigator && 'PushManager' in window
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!(await pushSupported())) return null
  const reg = await navigator.serviceWorker.getRegistration()
  return (await reg?.pushManager.getSubscription()) ?? null
}

export async function enablePush(vapidPublicKey: string): Promise<void> {
  if (!vapidPublicKey) throw new Error('Server has no VAPID key configured')
  const reg = await navigator.serviceWorker.register('/sw.js')
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') throw new Error('Notification permission denied')
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(vapidPublicKey) as BufferSource,
  })
  const json = sub.toJSON()
  await api('/api/push/subscribe', {
    method: 'POST',
    body: JSON.stringify({
      user_key: userKey(),
      endpoint: sub.endpoint,
      p256dh: json.keys?.p256dh,
      auth: json.keys?.auth,
    }),
  })
}

export async function disablePush(): Promise<void> {
  const sub = await currentSubscription()
  if (sub) {
    await api('/api/push/unsubscribe', {
      method: 'POST',
      body: JSON.stringify({ endpoint: sub.endpoint }),
    })
    await sub.unsubscribe()
  }
}
