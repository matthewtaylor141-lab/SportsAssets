import { useEffect, useState } from 'react'
import { api, userKey } from '../lib/api'
import { currentSubscription, disablePush, enablePush, pushSupported } from '../lib/push'
import type { Prefs, Whale } from '../lib/types'
import { SPORTS } from '../sports'

export function Alerts() {
  const [config, setConfig] = useState<{ vapid_public_key: string; telegram_channel_invite_url: string } | null>(null)
  const [whales, setWhales] = useState<Whale[]>([])
  const [prefs, setPrefs] = useState<Prefs | null>(null)
  const [pushOn, setPushOn] = useState(false)
  const [supported, setSupported] = useState(true)
  const [status, setStatus] = useState('')

  useEffect(() => {
    api<any>('/api/config').then(setConfig).catch(() => {})
    api<Whale[]>('/api/whales').then(setWhales).catch(() => {})
    api<Prefs>(`/api/prefs/${userKey()}`).then(setPrefs).catch(() => {})
    pushSupported().then((ok) => {
      setSupported(ok)
      if (ok) currentSubscription().then((s) => setPushOn(!!s))
    })
  }, [])

  const savePrefs = async (next: Prefs) => {
    setPrefs(next)
    await api(`/api/prefs/${userKey()}`, { method: 'PUT', body: JSON.stringify(next) })
    setStatus('Saved.')
    setTimeout(() => setStatus(''), 1500)
  }

  const togglePush = async () => {
    try {
      if (pushOn) {
        await disablePush()
        setPushOn(false)
      } else {
        await enablePush(config?.vapid_public_key || '')
        setPushOn(true)
      }
    } catch (e) {
      setStatus(e instanceof Error ? e.message : 'Push setup failed')
    }
  }

  const toggleMute = (id: number) => {
    if (!prefs) return
    const muted = prefs.muted_whales.includes(id)
      ? prefs.muted_whales.filter((m) => m !== id)
      : [...prefs.muted_whales, id]
    savePrefs({ ...prefs, muted_whales: muted })
  }

  const toggleSport = (s: string) => {
    if (!prefs) return
    const sports = prefs.sports.includes(s) ? prefs.sports.filter((x) => x !== s) : [...prefs.sports, s]
    savePrefs({ ...prefs, sports })
  }

  return (
    <>
      <h1>Alerts</h1>
      <p className="sub">
        Filters apply to notifications only — the live feed always shows every trade.
      </p>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Browser push</h2>
        {!supported ? (
          <p style={{ color: 'var(--muted)' }}>This browser doesn't support web push.</p>
        ) : (
          <button className={`btn ${pushOn ? 'danger' : 'primary'}`} onClick={togglePush}>
            {pushOn ? 'Disable push notifications' : 'Enable push notifications'}
          </button>
        )}
        <p style={{ color: 'var(--muted)', fontSize: 12 }}>
          Default is every trade the moment it's detected. Bursts (&gt;5 alerts from one whale in
          60s) are collapsed into a single summary.
        </p>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Telegram</h2>
        {config?.telegram_channel_invite_url ? (
          <a className="btn" href={config.telegram_channel_invite_url} target="_blank" rel="noreferrer">
            Join the Telegram alert channel →
          </a>
        ) : (
          <p style={{ color: 'var(--muted)' }}>Telegram channel not configured yet.</p>
        )}
      </div>

      {prefs && (
        <>
          <div className="card">
            <h2 style={{ marginTop: 0 }}>Minimum trade size</h2>
            <input
              className="input"
              type="number"
              min={0}
              value={prefs.min_notional}
              onChange={(e) => savePrefs({ ...prefs, min_notional: Number(e.target.value) || 0 })}
              aria-label="Minimum notional for alerts"
            />{' '}
            <span style={{ color: 'var(--muted)' }}>USDC — 0 means any trade</span>
          </div>

          <div className="card">
            <h2 style={{ marginTop: 0 }}>Per-whale mute</h2>
            {whales.length === 0 ? (
              <p style={{ color: 'var(--muted)' }}>Roster is empty.</p>
            ) : (
              <div className="feed-filters">
                {whales.map((w) => (
                  <button
                    key={w.id}
                    className="btn"
                    style={prefs.muted_whales.includes(w.id) ? { opacity: 0.45, textDecoration: 'line-through' } : {}}
                    onClick={() => toggleMute(w.id)}
                  >
                    {w.username || w.address.slice(0, 8)}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="card">
            <h2 style={{ marginTop: 0 }}>Sports</h2>
            <p style={{ color: 'var(--muted)', fontSize: 12, marginTop: 0 }}>
              None selected = all sports.
            </p>
            <div className="feed-filters">
              {SPORTS.map((s) => (
                <button
                  key={s}
                  className="btn"
                  style={prefs.sports.includes(s) ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}}
                  onClick={() => toggleSport(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
      {status && <p style={{ color: 'var(--good)' }}>{status}</p>}
    </>
  )
}
