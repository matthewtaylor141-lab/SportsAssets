/** Statistical edge as an instrument, honesty built in: below
 * `minSettled` the gauge crosshatches and refuses a verdict —
 * `SAMPLE n=x` instead. Band width scales with 1/sqrt(n). */
export function EdgeMeter({ roi, n, minSettled = 30, color = 'var(--cat2)', w = 160 }: {
  roi: number | null
  n: number
  minSettled?: number
  color?: string
  w?: number
}) {
  const H = 28
  const pad = 6
  const mid = w / 2
  if (roi == null || n < minSettled) {
    return (
      <svg className="nd-edge" width={w} height={H} role="img"
        aria-label={`sample too small: n=${n}`}>
        <defs>
          <pattern id="ndhatch" width="5" height="5" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
            <line x1="0" y1="0" x2="0" y2="5" stroke="var(--line-strong)" strokeWidth="1" />
          </pattern>
        </defs>
        <rect x={pad} y={H / 2 - 4} width={w - 2 * pad} height={8} rx={2} className="hatch" fill="url(#ndhatch)" />
        <text x={mid} y={H - 3} textAnchor="middle">SAMPLE n={n}</text>
      </svg>
    )
  }
  const scale = (r: number) => mid + Math.max(-1, Math.min(1, r / 0.5)) * (mid - pad)
  const half = Math.min(mid - pad, (mid - pad) * (1.96 / Math.sqrt(Math.max(n, 1))) * 4)
  const x = scale(roi)
  return (
    <svg className="nd-edge" width={w} height={H} role="img"
      aria-label={`roi ${(roi * 100).toFixed(1)}% over ${n} settled`}>
      <line x1={pad} y1={H / 2} x2={w - pad} y2={H / 2} className="track" strokeWidth="1" />
      <line x1={mid} y1={4} x2={mid} y2={H - 10} stroke="var(--line-strong)" strokeDasharray="1 3" />
      <rect x={Math.max(pad, x - half)} y={H / 2 - 5} width={Math.min(w - pad, x + half) - Math.max(pad, x - half)} height={10} className="band" rx={2} />
      <path d={`M ${x} ${H / 2 - 6} l 5 6 l -5 6 l -5 -6 z`} fill={color} />
      <text x={pad} y={H - 3}>{(roi * 100).toFixed(1)}%</text>
      <text x={w - pad} y={H - 3} textAnchor="end">n={n}</text>
    </svg>
  )
}
