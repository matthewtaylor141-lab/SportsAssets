import { useCallback, useEffect, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { adminApi } from '../lib/api'
import { fmtAgo, fmtUsd, shortAddr } from '../lib/format'
import type { LatencySummary, Whale } from '../lib/types'

interface Health {
  heartbeats: { service: string; status: string; detail: any; beat_at: string }[]
  reconciliation: { id: number; started_at: string; missed: number }[]
  outbox: { pending: number; sent: number; collapsed: number }
  push_subscriptions: number
}

export function Admin() {
  const [token, setToken] = useState(() => sessionStorage.getItem('sa_admin_token') || '')
  const [authed, setAuthed] = useState(false)
  const [health, setHealth] = useState<Health | null>(null)
  const [latency, setLatency] = useState<{ overall: LatencySummary; chain: LatencySummary; poll: LatencySummary } | null>(null)
  const [roster, setRoster] = useState<{ whales: Whale[] } | null>(null)
  const [err, setErr] = useState('')

  const refresh = useCallback(async (tok: string) => {
    try {
      const [h, l, r] = await Promise.all([
        adminApi<Health>('/api/admin/health', tok),
        adminApi<any>('/api/admin/latency', tok),
        adminApi<{ whales: Whale[] }>('/api/admin/roster', tok),
      ])
      setHealth(h)
      setLatency(l)
      setRoster(r)
      setAuthed(true)
      setErr('')
      sessionStorage.setItem('sa_admin_token', tok)
    } catch {
      setErr('Unauthorized or API unreachable.')
      setAuthed(false)
    }
  }, [])

  useEffect(() => {
    if (token) refresh(token)
    const t = setInterval(() => token && refresh(token), 10000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed])

  const action = async (act: string, body: object) => {
    await adminApi(`/api/admin/roster/${act}`, token, { method: 'POST', body: JSON.stringify(body) })
    refresh(token)
  }

  if (!authed) {
    return (
      <>
        <h1>Admin</h1>
        <div className="card" style={{ maxWidth: 420 }}>
          <p>Enter the admin token (env <code>ADMIN_TOKEN</code>).</p>
          <input
            className="input"
            type="password"
            value={token}
            autoCapitalize="none"
            autoCorrect="off"
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setToken(e.target.value.trim())}
            onKeyDown={(e) => e.key === 'Enter' && refresh(token.trim())}
            aria-label="Admin token"
          />{' '}
          <button className="btn primary" onClick={() => refresh(token.trim())}>
            Unlock
          </button>
          {err && <p style={{ color: 'var(--critical)' }}>⚠ {err}</p>}
        </div>
      </>
    )
  }

  const fmtLat = (s: LatencySummary | undefined) =>
    !s || s.count === 0 ? '—' : `p50 ${s.p50?.toFixed(1)}s · p95 ${s.p95?.toFixed(1)}s (${s.count})`

  return (
    <>
      <h1>Admin</h1>
      <p className="sub">Ingestion health, detection latency, roster management, delivery stats.</p>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Ingestion health</h2>
        {!health || health.heartbeats.length === 0 ? (
          <EmptyState>No heartbeats yet — services haven't started.</EmptyState>
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>Service</th>
                <th>Status</th>
                <th>Last beat</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {health.heartbeats.map((b) => (
                <tr key={b.service}>
                  <td style={{ fontWeight: 600 }}>{b.service}</td>
                  <td>
                    <span
                      className="chip"
                      style={
                        b.status === 'ok'
                          ? { color: 'var(--good)', borderColor: 'var(--good)' }
                          : b.status === 'down' || b.status === 'error'
                            ? { color: 'var(--critical)', borderColor: 'var(--critical)' }
                            : {}
                      }
                    >
                      {b.status === 'ok' ? '✓ ok' : b.status === 'down' || b.status === 'error' ? `✗ ${b.status}` : b.status}
                    </span>
                  </td>
                  <td>{fmtAgo(b.beat_at)}</td>
                  <td className="mono" style={{ color: 'var(--muted)', maxWidth: 340, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="grid2">
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Detection latency (24h)</h2>
          <div className="statgrid">
            <div className="stat">
              <div className="label">Overall</div>
              <div className="value" style={{ fontSize: 14 }}>{fmtLat(latency?.overall)}</div>
            </div>
            <div className="stat">
              <div className="label">Path A (chain)</div>
              <div className="value" style={{ fontSize: 14 }}>{fmtLat(latency?.chain)}</div>
            </div>
            <div className="stat">
              <div className="label">Path B (poll)</div>
              <div className="value" style={{ fontSize: 14 }}>{fmtLat(latency?.poll)}</div>
            </div>
          </div>
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Notifications</h2>
          <div className="statgrid">
            <div className="stat">
              <div className="label">Push subs</div>
              <div className="value">{health?.push_subscriptions ?? 0}</div>
            </div>
            <div className="stat">
              <div className="label">Outbox pending</div>
              <div className="value">{health?.outbox?.pending ?? 0}</div>
            </div>
            <div className="stat">
              <div className="label">Sent</div>
              <div className="value">{health?.outbox?.sent ?? 0}</div>
            </div>
            <div className="stat">
              <div className="label">Collapsed</div>
              <div className="value">{health?.outbox?.collapsed ?? 0}</div>
            </div>
          </div>
          <SmsTest token={token} />
          <h2>Reconciliation (last runs)</h2>
          {health?.reconciliation?.length ? (
            <table className="data">
              <tbody>
                {health.reconciliation.map((r) => (
                  <tr key={r.id}>
                    <td>{fmtAgo(r.started_at)}</td>
                    <td className={`num ${r.missed > 0 ? 'neg' : 'pos'}`}>
                      {r.missed > 0 ? `✗ ${r.missed} missed` : '✓ clean'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p style={{ color: 'var(--muted)' }}>No runs yet.</p>
          )}
        </div>
      </div>

      <div className="card">
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <h2 style={{ margin: 0, flex: 1 }}>Roster</h2>
          <button className="btn" onClick={() => action('refresh', {})}>
            ↻ Refresh from leaderboard
          </button>
        </div>
        {roster?.whales.length ? (
          <div className="scroll-x">
            <table className="data" style={{ marginTop: 8 }}>
              <thead>
                <tr>
                  <th>Trader</th>
                  <th>Wallet</th>
                  <th className="num">LB profit</th>
                  <th>State</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {roster.whales.map((w) => (
                  <tr key={w.id} style={w.active ? {} : { opacity: 0.5 }}>
                    <td style={{ fontWeight: 600 }}>{w.username || 'anonymous'}</td>
                    <td className="mono">{shortAddr(w.address)}</td>
                    <td className="num">{fmtUsd(w.sports_profit_alltime)}</td>
                    <td>
                      {w.banned ? '🚫 banned' : w.active ? '● tracked' : '○ inactive'}
                      {w.pinned && ' 📌'}
                    </td>
                    <td style={{ display: 'flex', gap: 4 }}>
                      <button className="btn" onClick={() => action(w.pinned ? 'unpin' : 'pin', { whale_id: w.id })}>
                        {w.pinned ? 'Unpin' : 'Pin'}
                      </button>
                      <button className="btn danger" onClick={() => action(w.banned ? 'unban' : 'ban', { whale_id: w.id })}>
                        {w.banned ? 'Unban' : 'Ban'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState>
            Roster empty. Hit “Refresh from leaderboard” (requires the leaderboard API to be
            reachable from the backend) or pin a wallet below.
          </EmptyState>
        )}
        <PinForm onPin={(address, username) => action('pin', { address, username })} />
      </div>
    </>
  )
}

function SmsTest({ token }: { token: string }) {
  const [result, setResult] = useState<string>('')
  const [busy, setBusy] = useState('')
  const test = async (channel: 'sms' | 'ntfy') => {
    setBusy(channel)
    setResult('')
    try {
      const r = await adminApi<any>(`/api/admin/${channel}-test`, token, { method: 'POST', body: '{}' })
      if (r.ok) setResult('✓ Test sent — check your phone.')
      else if (r.configured === false) setResult(`✗ ${r.error}`)
      else if (channel === 'sms')
        setResult(`✗ ${r.results?.filter((x: any) => !x.ok).map((x: any) => `${x.to}: ${x.error}`).join('; ')}`)
      else setResult(`✗ ${r.error}`)
    } catch {
      setResult('✗ Request failed.')
    } finally {
      setBusy('')
    }
  }
  return (
    <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <button className="btn" onClick={() => test('ntfy')} disabled={busy !== ''}>
        {busy === 'ntfy' ? 'Sending…' : '🔔 Test ntfy push'}
      </button>
      <button className="btn" onClick={() => test('sms')} disabled={busy !== ''}>
        {busy === 'sms' ? 'Sending…' : '📱 Test SMS'}
      </button>
      {result && (
        <span style={{ fontSize: 13, color: result.startsWith('✓') ? 'var(--good)' : 'var(--critical)' }}>
          {result}
        </span>
      )}
    </div>
  )
}

function PinForm({ onPin }: { onPin: (address: string, username: string) => void }) {
  const [addr, setAddr] = useState('')
  const [name, setName] = useState('')
  return (
    <div className="feed-filters" style={{ marginTop: 10 }}>
      <input className="input" placeholder="0x wallet address" value={addr} onChange={(e) => setAddr(e.target.value)} style={{ width: 320 }} />
      <input className="input" placeholder="label (optional)" value={name} onChange={(e) => setName(e.target.value)} />
      <button
        className="btn"
        disabled={!/^0x[0-9a-fA-F]{40}$/.test(addr)}
        onClick={() => {
          onPin(addr, name)
          setAddr('')
          setName('')
        }}
      >
        📌 Pin wallet
      </button>
    </div>
  )
}
