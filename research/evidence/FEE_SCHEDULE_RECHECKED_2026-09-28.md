# The fee schedule, re-checked against the venue's page — 2026-09-28

**Why now.** Today's commission repair means the venue's *observed* fee finally reaches the funded book's reconciler (`executions_of` was dropping it on every funded submit, so `observed_fee_usd` was always `None`). From today, an observed charge is compared against the schedule's *expectation* and `FEE_DISAGREES` can actually fire. **That makes the expectation's correctness matter in a way it did not when nothing was ever compared to it**, so I re-read the source rather than trusting a constant set from a directive.

**Source:** `docs.polymarket.us/fees`, retrieved `2026-09-28T12:48:35Z` via `fetch-docs` run 102.

---

## Every constant matches

| Quantity | Venue's page | `calibration_fees` | |
|---|---|---|:--:|
| Taker coefficient | `0.0695` | `TAKER_COEFFICIENT = "0.0695"` | ✓ |
| Maker rebate coefficient | `0.0125` (credited) | `MAKER_REBATE_COEFFICIENT = "-0.0125"` | ✓ |
| Standard formula | `coefficient × quantity × price × (1 − price)` | `FORMULA` identical | ✓ |
| Rounding | *"banker's rounding (round half to even)"* | `ROUNDING_IMPLEMENTED = "ROUND_HALF_EVEN"`, `quantize(CENT, rounding=ROUND_HALF_EVEN)` | ✓ |
| Combo curve | `C × p × [0.0695 × (1 − p) + 0.04 × (1 − p)^4]` | `COMBO_CURVE_PUBLISHED`, string-identical | ✓ |

The page's worked examples reconcile against the same curve: 1,000 combo contracts at \$0.10 → `1000 × 0.10 × [0.0695 × 0.90 + 0.04 × 0.90⁴] = $8.8794 → −$8.88`, and the midpoint case at \$18.625 rounding **down** to \$18.62, which is banker's rounding behaving as the module implements it rather than as `ROUND_HALF_UP` would (that would give \$18.63). An earlier revision of the module did use `ROUND_HALF_UP` and an audit corrected it; this read confirms the correction against the venue's own example.

**No defect found.** This is a confirmation, not a repair.

---

## One line that validates a design decision I made today

> **"Can fees ever be zero?** Yes. Fees are rounded to the nearest cent. On small trades (low quantity or prices near \$0.00 or \$1.00), the fee can round down to \$0.00."

**A stated zero is a real fee on this venue.** That is precisely why `_venue_commission` distinguishes *"the venue stated 0.00"* from *"the venue stated nothing"* and returns `None` for the second rather than defaulting to zero:

- **stated 0.00** → an observation. It can reconcile against the schedule, and where the schedule expected a non-zero amount it should raise `FEE_DISAGREES`.
- **stated nothing** → no observation. The booked fee stays the schedule's expectation, in state `PROVISIONAL`, and the cash is not final.

Collapsing those two would turn *unknown* into *free*, which understates cost — and on this venue it would also **discard a legitimate zero**, losing a real reconciliation. `test_a_stated_zero_commission_is_an_observation_not_an_absence` pins both directions.

## And one that bears on when a fee is final

> *"Taker fees are deducted from your balance at the time of the trade. Maker rebates are credited to your balance at the time of the fill."*

Fees settle **with the trade**, not later. So once the venue states a commission on an execution, that figure is the cash — which is the basis on which `_fee_for_fill` books `booked_fee_usd = float(observed)` and treats the expectation as superseded. The lane's existing behaviour is right; this records the venue statement it rests on.

## What this does not establish

- **Not** that the account's actual charges match the published schedule. That needs a real fill with a stated commission, and no funded order has ever been sent. What is now true is that when one arrives, the comparison will actually run.
- **Not** the `feeCoefficient 0.06` seen in an earlier probe payload. The page publishes `0.0695`; the formula behind that other field is still unread, and it is recorded in `pmus._commission_fields` as unresolved rather than reconciled here.
