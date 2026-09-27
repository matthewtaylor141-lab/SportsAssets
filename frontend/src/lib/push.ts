import { api, notifyHeaders, rememberNotifyCapability, userKey } from './api'

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
  // FIRST REGISTRATION RETURNS THE CAPABILITY, ONCE. Store it: it is what
  // authorises unsubscribing and reading or writing preferences, and the server
  // will not reissue it -- reissuing on a repeat registration would hand control
  // to anyone holding the public user_key.
  const res = await api<{ capability?: string | null }>('/api/push/subscribe', {
    method: 'POST',
    body: JSON.stringify({
      user_key: userKey(),
      endpoint: sub.endpoint,
      p256dh: json.keys?.p256dh,
      auth: json.keys?.auth,
    }),
  })
  rememberNotifyCapability(res?.capability)
}

export async function disablePush(): Promise<void> {
  const sub = await currentSubscription()
  if (sub) {
    // THE CAPABILITY AUTHORISES THIS, NOT THE user_key.
    //
    // The server used to delete by endpoint alone, so anybody who learned an
    // endpoint could switch off somebody else's alerts. Sending the user_key
    // fixed less than it looked: the key is a PUBLIC identifier that travels in
    // URL paths and is therefore in logs. Control now needs the server-issued
    // capability, in a header.
    await api('/api/push/unsubscribe', {
      method: 'POST',
      headers: notifyHeaders(),
      body: JSON.stringify({ endpoint: sub.endpoint, user_key: userKey() }),
    })
    await sub.unsubscribe()
  }
}
