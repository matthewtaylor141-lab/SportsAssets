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

export function Brand() {
  if (logoUrl) {
    return <img className="brand brand-logo" src={logoUrl} alt="BettorToken" />
  }
  return (
    <span className="brand bt-brand" aria-label="BettorToken">
      BETTOR<span>TOKEN</span>
    </span>
  )
}
