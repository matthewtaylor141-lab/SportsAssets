# THE VENUE'S PUBLISHED FEE POLICY — retrieved 2026-09-27, verbatim

**Source:** `https://docs.polymarket.us/fees` — HTTP 200, 460,049 bytes.
**Retrieved:** 2026-09-27T15:59:54Z, on the GitHub runner (`command-verify`
workflow, `fee-docs` job, run 36331564367). The build container cannot reach this
host: `CONNECT` returns 403 under the egress policy, which is why this question
stood open in source and why tie vectors were recorded instead of a policy.

**Response metadata, so the retrieval is datable:**

```
date:          Sun, 27 Sep 2026 15:59:54 GMT
age:           77731
etag:          W/"uw657j3lkx9unr"
cache-control: no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0
```

Sentences below are **quoted, not summarised**. A summary of a fee policy is how
the `min(p, 1−p)` defect got written.

---

## 1 · Rounding — the audit was right, and we were wrong

> "All fees and rebates are rounded to the nearest $0.01 using **banker's
> rounding (round half to even)**."

> "It rounds down because fees use banker's rounding (round half to even)."

> "Fees are rounded to the nearest cent."

> "On small trades (low quantity or prices near $0.00 or $1.00), the fee can
> round down to $0.00."

**`calibration_fees` implemented `ROUND_HALF_UP`. That is a defect and it is
now corrected to `ROUND_HALF_EVEN`.** Our six discriminating vectors resolve to
the half-even column: 120 @ 0.50 → **$2.08**, not $2.09.

## 2 · Cumulative taker-fill adjustment — an exact algorithm, now implemented

> "When an aggressive order fills against multiple resting orders, **each fill is
> charged its banker's-rounded fee, adjusted so that the total commission
> collected across the order's fills never exceeds the banker's rounding of the
> cumulative exact fee.**"

> "**Maker rebates are computed per fill, independently.**"

So the taker algorithm is: per-fill banker's-rounded fee, with a **running cap**
at the banker's rounding of the cumulative *exact* fee. The cap applies to the
taker side only — maker rebates are independent per fill and carry no cap.

## 3 · Formula and coefficients — our formula was already right

> "Standard taker fees and maker rebates are computed using a symmetric formula
> that scales with price uncertainty: **Fee = Θ × C × p × (1 − p)** … C is the
> number of contracts, p is the trade price ($0.01 to $0.99), Θ (theta) is the
> fee coefficient. Theta Max (p = $0.50): Taker Fee **0.0695** $1.74, Maker
> Rebate **−0.0125** −$0.31. Maker rebate is applied at the point of trade."

## 4 · Effective dates — Θ is PER SPORT, and one changes in three days

> "Effective exchange-wide from **12 AM ET, Friday September 25, 2026**."

> "**Upcoming Table Tennis fee change.** The Table Tennis taker fee coefficient
> becomes **0.10**, effective **11:59 PM ET, Wednesday September 30, 2026**. This
> page will be updated when the change takes effect."

**This is material and we had no representation of it.** The coefficient is not a
single exchange-wide constant: it is per sport, and one is scheduled to change.
A single `TAKER = 0.0695` would have silently mispriced Table Tennis from
2026-09-30. Table Tennis is outside the proposed operating scope, and the
schedule is now versioned by sport and effective date regardless.

## 5 · Execution-report units

> "**Execution reports carry these as fixed-point integers, and the collected fee
> in scaled notional units** — see Fees on execution reports for how to decode
> `commission_notional_collected` with `price_scale` and
> `fractional_quantity_scale`."

> "API integrators: C is the number of contracts and p the decimal price."

## 6 · Tiered taker rebate — zero at pilot scale

> "Participants who trade over $250,000 in taker volume during the prior calendar
> month receive rebates according to the following schedule … $250,000–$999,999
> 10%, $1,000,000–$9,999,999 25%, $10,000,000+ 50% … Rebates are paid out
> weekly."

> "A Participant's tier for a given month is determined by their notional taker
> volume in the immediately preceding calendar month."

At the proposed pilot size this is **0%**, and it is a *later* payment — not a
reduction in the charge on a fill, so it must never be netted into an expected
fee.

## 7 · Combo taker fees — a separate curve we do NOT implement

> "The taker side of a combo trade uses a separate fee curve:
> **Fee = C × p × [0.0695 × (1 − p) + 0.04 × (1 − p)^4]** … C is the number of
> contracts and p is the combo execution price in decimal dollars."

**Not implemented, and refused by name** rather than priced with the standard
curve. Combos are outside the proposed operating scope; pricing one with the
wrong curve would understate the charge.

---

## What changed in the code because of this

| | before | after |
|---|---|---|
| rounding | `ROUND_HALF_UP` | `ROUND_HALF_EVEN`, quoting this page |
| multi-fill taker | per-fill, no cap, marked PROVISIONAL | per-fill banker's rounding with the running cumulative cap |
| maker rebates | per fill | per fill, **explicitly uncapped** |
| coefficient | one constant | per-sport schedule with effective dates |
| Table Tennis 2026-09-30 | not represented | represented, with its effective instant |
| combos | would have used the standard curve | refused by name |
| execution-report units | unstated | decoding documented and required |
| `FEE_ARITHMETIC_IS_EXACT` | `False` | `True` for the standard curve in scope |
