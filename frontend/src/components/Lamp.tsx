/** NIGHT DESK status lamp. `sonar` rings outward (armed/live states),
 * `breathe` pulses (a whale that traded in the last 10 minutes),
 * `static` is a plain dot. Color rides currentColor via `color`. */
export function Lamp({ color, mode = 'static', label }: {
  color: string
  mode?: 'sonar' | 'breathe' | 'static'
  label?: string
}) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color }}>
      <span
        className={`lamp${mode === 'sonar' ? ' lamp-sonar' : mode === 'breathe' ? ' lamp-breathe' : ''}`}
        role={label ? 'status' : undefined}
        aria-label={label}
      />
      {label && (
        <span style={{
          font: '500 10px var(--font-data)', letterSpacing: '0.12em',
          textTransform: 'uppercase',
        }}>{label}</span>
      )}
    </span>
  )
}
