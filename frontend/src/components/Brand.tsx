/* BETTOR TOKEN brand (owner logo + directive 2026-08-28).
 *
 * THE WORDMARK IS THE OFFICIAL ARTWORK, NEVER RE-TYPESET: anywhere a
 * header says BettorToken, the actual logo lettering renders as an
 * image. The hexagonal-b icon may stand ALONE elsewhere (favicons,
 * chips, tab strips). Both assets are cut from the delivered logo
 * file, color-snapped to the brand #0066FF on transparency.
 */
import mark from '../assets/bt-mark.png'
import wordmark from '../assets/bt-wordmark.png'
import lockup from '../assets/bt-logo-full.png'

export function BrandMark({ size = 20 }: { size?: number }) {
  return (
    <img
      className="bt-mark-img"
      src={mark}
      width={size}
      height={size}
      alt=""
      aria-hidden
      style={{ display: 'block' }}
    />
  )
}

/** The official full lockup (icon + lettering) for hero surfaces. */
export function BrandLockup({ height = 34 }: { height?: number }) {
  return (
    <img
      className="bt-lockup-img"
      src={lockup}
      height={height}
      alt="Bettor Token — Tokenized Asset Management"
      style={{ display: 'block', width: 'auto', height }}
    />
  )
}

/** Nav brand: the icon + the ACTUAL wordmark artwork. */
export function Brand() {
  return (
    <span className="brand bt-brand" aria-label="Bettor Token">
      <BrandMark />
      <img
        className="bt-word-img"
        src={wordmark}
        alt="Bettor Token"
        style={{ display: 'block', height: 16, width: 'auto' }}
      />
    </span>
  )
}
