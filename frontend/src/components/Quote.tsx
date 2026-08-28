import { useEffect, useRef, useState } from 'react'

/** Exchange-board figure: digits are REPLACED (never tweened) and the
 * change flashes pos/neg, decaying over 500ms. Title (and tap on
 * touch) reveals the last change and its age. */
export function Quote({ value, render }: {
  value: number
  render: (v: number) => string
}) {
  const prev = useRef(value)
  const [flash, setFlash] = useState<'up' | 'down' | null>(null)
  const [changed, setChanged] = useState<{ d: number; at: number } | null>(null)

  useEffect(() => {
    if (value === prev.current) return
    const d = value - prev.current
    prev.current = value
    setChanged({ d, at: Date.now() })
    setFlash(d > 0 ? 'up' : 'down')
  }, [value])

  const title = changed
    ? `${changed.d >= 0 ? '+' : '−'}${Math.abs(changed.d).toFixed(2)} · ${Math.max(0, Math.round((Date.now() - changed.at) / 1000))}s ago`
    : undefined
  return (
    <span
      className="quote"
      data-flash={flash ?? undefined}
      onAnimationEnd={() => setFlash(null)}
      title={title}
    >{render(value)}</span>
  )
}
