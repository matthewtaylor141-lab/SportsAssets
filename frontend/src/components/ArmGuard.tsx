import { useEffect, useRef, useState } from 'react'

/** ARM → CONFIRM two-step for every destructive control: the second
 * click must land inside 5 seconds or the guard silently disarms
 * (Esc, blur, and unmount also disarm). Wraps the handler only —
 * payloads and endpoints are untouched. */
export function ArmGuard({ label, onConfirm, disabled }: {
  label: string
  onConfirm: () => void
  disabled?: boolean
}) {
  const [armed, setArmed] = useState(false)
  const [left, setLeft] = useState(5)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const disarm = () => {
    setArmed(false)
    if (timer.current) { clearInterval(timer.current); timer.current = null }
  }

  useEffect(() => {
    if (!armed) return
    setLeft(5)
    const started = Date.now()
    timer.current = setInterval(() => {
      const remain = 5 - Math.floor((Date.now() - started) / 1000)
      if (remain <= 0) disarm()
      else setLeft(remain)
    }, 250)
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') disarm() }
    window.addEventListener('keydown', onEsc)
    return () => {
      window.removeEventListener('keydown', onEsc)
      if (timer.current) { clearInterval(timer.current); timer.current = null }
    }
  }, [armed])

  return (
    <button
      className="btn btn-danger-outline guard"
      data-state={armed ? 'armed' : 'idle'}
      disabled={disabled}
      aria-live="polite"
      onBlur={disarm}
      onClick={() => {
        if (!armed) { setArmed(true); return }
        disarm()
        onConfirm()
      }}
    >{armed ? `CONFIRM (${left})` : `ARM · ${label}`}</button>
  )
}
