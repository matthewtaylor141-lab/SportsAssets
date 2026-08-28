/* BETTORTOKEN wordmark lockup (owner order 2026-08-22: the company is
 * BettorToken — retire the "BETTOREDGE AI" placeholder).
 *
 * Typographic by default: Space Grotesk 700, tight tracking, "BETTOR"
 * in ink + "TOKEN" in the accent. The moment a real logo file lands at
 * src/assets/logo.svg it takes over automatically — import.meta.glob
 * resolves to an empty record while the file is absent, so the slot
 * costs nothing today and needs no code change later. */

const logoFiles = import.meta.glob('../assets/logo.svg', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>
const logoUrl = Object.values(logoFiles)[0]

/** v9 monogram (owner order 2026-08-28, brand identity): a sonar arc
 * sweeping a filled core — the product in one glyph: we listen for the
 * whale's fill and strike. Gold carries money, cyan carries the live
 * sweep, matching the wall palette promoted app-wide. */
function Mark({ size = 18 }: { size?: number }) {
  return (
    <svg className="bt-mark" width={size} height={size} viewBox="0 0 24 24"
      fill="none" aria-hidden>
      <circle className="core" cx="12" cy="12" r="3.2" />
      <path className="arc" d="M19.5 12a7.5 7.5 0 0 0-7.5-7.5"
        strokeWidth="2" strokeLinecap="round" />
      <path className="arc2" d="M4.5 12a7.5 7.5 0 0 0 7.5 7.5"
        strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

export function Brand() {
  if (logoUrl) {
    return <img className="brand brand-logo" src={logoUrl} alt="BettorToken" />
  }
  return (
    <span className="brand bt-brand" aria-label="BettorToken">
      <Mark />
      BETTOR<span>TOKEN</span>
    </span>
  )
}
