import { useEffect } from 'react'
import type { ReactNode } from 'react'

/** The one inspection mechanic: a right drawer (bottom sheet on
 * mobile) with an entity's detail. Esc/scrim closes. */
export function Drawer({ title, onClose, children }: {
  title: string
  onClose: () => void
  children: ReactNode
}) {
  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onEsc)
    return () => window.removeEventListener('keydown', onEsc)
  }, [onClose])
  return (
    <>
      <div className="nd-drawer-scrim" onClick={onClose} />
      <aside className="nd-drawer" role="dialog" aria-label={title}>
        <div className="card-title" style={{ marginBottom: 12 }}>{title}
          <button className="btn" style={{ marginLeft: 'auto', minHeight: 26, padding: '4px 8px' }} onClick={onClose}>✕</button>
        </div>
        {children}
      </aside>
    </>
  )
}
