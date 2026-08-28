/* BETTOR TOKEN brand lockup (owner logo landed 2026-08-28).
 *
 * The mark is the company's hexagonal b — icon only, no lettering —
 * cut from the official logo and served brand-blue on transparency.
 * The wordmark is set in the data register beside it; TOKEN carries
 * the brand blue exactly like the official lettering. */
import mark from '../assets/bt-mark.png'

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

export function Brand() {
  return (
    <span className="brand bt-brand" aria-label="Bettor Token">
      <BrandMark />
      BETTOR<span>&nbsp;TOKEN</span>
    </span>
  )
}
