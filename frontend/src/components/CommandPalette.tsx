import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

type Cmd = { label: string; to: string; kind: string }

const ROUTES: Cmd[] = [
  { label: 'Performance', to: '/', kind: 'page' },
  { label: 'Analytics', to: '/analytics', kind: 'page' },
  { label: 'Reports', to: '/reports', kind: 'page' },
  { label: 'Accounts', to: '/accounts', kind: 'page' },
  { label: 'Desk', to: '/desk', kind: 'page' },
  { label: 'Meridian', to: '/meridian', kind: 'page' },
  { label: 'System', to: '/system', kind: 'page' },
  { label: 'Engine', to: '/engine', kind: 'ops' },
  { label: 'Ops', to: '/admin', kind: 'ops' },
]

/** Terminal prompt over the site: Cmd/Ctrl-K or `/` opens, fuzzy
 * filter, arrows + Enter, Esc closes. Zero deps. */
export function CommandPalette() {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const nav = useNavigate()
  const input = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const inField = /input|textarea|select/i.test(
        (e.target as HTMLElement)?.tagName || '')
      if ((e.key === 'k' && (e.metaKey || e.ctrlKey)) ||
          (e.key === '/' && !inField && !open)) {
        e.preventDefault()
        setOpen(true); setQ(''); setSel(0)
      }
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  useEffect(() => { if (open) input.current?.focus() }, [open])

  const hits = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return ROUTES
    return ROUTES.filter((c) => {
      let i = 0
      for (const ch of c.label.toLowerCase()) if (ch === needle[i]) i++
      return i >= needle.length
    })
  }, [q])

  if (!open) return null
  return (
    <>
      <div className="nd-palette-scrim" onClick={() => setOpen(false)} />
      <div className="nd-palette" role="dialog" aria-label="Command palette">
        <span className="nd-palette-prompt" aria-hidden>&gt;</span>
        <input
          ref={input}
          value={q}
          placeholder="jump to…"
          onChange={(e) => { setQ(e.target.value); setSel(0) }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, hits.length - 1)) }
            if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)) }
            if (e.key === 'Enter' && hits[sel]) { nav(hits[sel].to); setOpen(false) }
          }}
        />
        <div className="nd-palette-list">
          {hits.map((c, i) => (
            <div
              key={c.to}
              className={`nd-palette-row${i === sel ? ' sel' : ''}`}
              onMouseEnter={() => setSel(i)}
              onClick={() => { nav(c.to); setOpen(false) }}
            >
              <span>{c.label}</span>
              <span className="k">{c.kind}</span>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
