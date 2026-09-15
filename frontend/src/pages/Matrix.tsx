import { useEffect, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { HeatMatrix } from '../components/HeatMatrix'
import { api } from '../lib/api'
import type { MatrixCell } from '../lib/types'

const WINDOWS = ['7d', '30d', 'all'] as const

/** The signature screen: whales × sports, cell = ROI with W-L, color-scaled. */
export function Matrix() {
  const [win, setWin] = useState<(typeof WINDOWS)[number]>('all')
  const [data, setData] = useState<{ sports: string[]; cells: MatrixCell[] } | null>(null)

  useEffect(() => {
    setData(null)
    api<{ sports: string[]; cells: MatrixCell[] }>(`/api/matrix?window=${win}`)
      .then(setData)
      .catch(() => setData({ sports: [], cells: [] }))
  }, [win])

  return (
    <>
      <h1>Sport Matrix</h1>
      <p className="sub">Who's best — and worst — at what. Realized ROI per whale per sport.</p>
      <div className="feed-filters">
        {WINDOWS.map((w) => (
          <button
            key={w}
            className="btn"
            style={win === w ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}}
            onClick={() => setWin(w)}
          >
            {w === 'all' ? 'All-time' : w}
          </button>
        ))}
      </div>
      <div className="card">
        {data === null ? (
          <EmptyState>Loading matrix…</EmptyState>
        ) : data.cells.length === 0 ? (
          <EmptyState>
            The matrix fills in as tracked whales' markets resolve. No settled data in this window
            yet.
          </EmptyState>
        ) : (
          <HeatMatrix cells={data.cells} sports={data.sports} />
        )}
      </div>
    </>
  )
}
