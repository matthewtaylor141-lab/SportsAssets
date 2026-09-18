# DUPLICATE-INSERT TEST — PROPOSAL, NOT AUTHORIZATION

**Status: NOT AUTHORIZED. NOT PERFORMED.** This document exists so the test
can be approved or refused on its actual terms. Nothing in it has been run.

## What question it answers, and why nothing else answers it

After a lost response we hold a `clordId` and no outcome. The retry decision
turns on one fact:

> If the same `clordId` is inserted again, does the exchange **reject the
> duplicate**, or does it **create a second order**?

Reject → a retry is recoverable and the single-send rule could, with
management's agreement, be relaxed to a bounded retry.
Accept → a retry is a second order and the single-send rule must stay
exactly as it is.

**`duplicate-preview` cannot answer this.** It establishes
`PREVIEW_REPEATABILITY` and nothing more: the preview endpoint places
nothing, so what it does with a repeat is not evidence about what the insert
endpoint does with one. A preview that accepts a repeat and an insert that
rejects one are entirely consistent; so are the opposites. Any claim that the
preview result bears on insert idempotency, duplicate-order protection, or
the single-send boundary is unsupported.

## Exact scope

| | |
|---|---|
| Environment | **PREPROD ONLY** — `api.preprod.polymarketexchange.com`. The `_assert_preprod` host guard stays; nothing in this test touches or weakens it. |
| Account | the preprod trading account only, on its **test funding**. No production credential is presented and none is configured. |
| Instrument | ONE preprod instrument, chosen for an **empty or near-empty book** so a resting order cannot cross. Named in the authorization request, not left open. |
| Orders sent | **at most two**, both carrying the **same** `clordId`. |
| Order shape | GTC + post-only (`participateDontInitiate: true`), limit priced **far from the touch** on the passive side, minimum quantity (1 contract). Post-only means a price that would cross is rejected rather than filled. |
| Production ledger | untouched. This runs in a PREPROD session (`environment = PREPROD`), so under migration 067 it cannot move the production `spent_usd` or the $100 allowance. |
| Concurrency | it occupies the ONE unresolved-lifecycle slot globally, so no production ticket can run beside it. That is intended. |

## Maximum dummy exposure

**Preprod test funding only — no real money is at risk at any point**, because
preprod has no real money in it. The exposure figure below is the *notional
the test could tie up in test dollars*, stated so the shape of the test is
legible, not because it is a loss that can occur:

- 1 contract × limit price ≤ 0.05 × 2 orders (if both rest) = **≤ 0.10 test
  dollars of notional**, against a `buyingPower` of 1,000,000 test dollars.
- Post-only at a far-from-touch passive price means a fill is not expected.
  **If one fills anyway, that is itself a finding** (post-only was not
  honoured), the cleanup below applies, and the test stops.

**No production exposure of any kind. This test does not establish, and must
not be cited as establishing, anything about production fills, production
fees, or profitability.**

## Observation plan

Everything below is recorded before, during and after — sanitized receipts,
no key, bearer, signed assertion or full account payload.

1. **Before:** open orders on the instrument, positions, balance. This is the
   pre-image; the test is not started without it.
2. **Insert #1** with `clordId = C`. Record the status, the body, the
   returned exchange id, and the timestamps either side of the call.
3. **Read back:** open orders, and the documented search bound to the
   account/instrument/window — **by `orderId` and by `clordId` as distinct
   singular fields**, following pagination to exhaustion.
4. **Insert #2** with the **same** `clordId = C`. Record status and body
   verbatim (a rejection's code and message are the answer).
5. **Read back again**, same two searches, plus the order stream if a
   subscription is established by then — whether a second order appears is
   the finding.
6. **Classify** exactly one of:
   - `DUPLICATE_REJECTED` — insert #2 refused, one order exists.
   - `DUPLICATE_ACCEPTED_SECOND_ORDER` — two distinct exchange ids exist.
   - `DUPLICATE_ACCEPTED_SAME_ORDER` — insert #2 succeeded and still one
     order exists (idempotent).
   - `INCONCLUSIVE` — anything else, including a truncated search walk or an
     unreadable read-back. **Inconclusive is a permitted outcome and is not
     to be rounded to one of the others.**

An empty search result classifies as `INCONCLUSIVE`, never as
`DUPLICATE_REJECTED`.

## Cleanup plan

1. Cancel every order the test created, by exchange id, one at a time.
2. **A cancel acknowledgement is not a terminal state.** Re-read until the
   venue reports each order terminal, or record it as unresolved.
3. If anything filled: record the position, and leave it. Do **not** trade
   out of it as part of this test — an unplanned exit is a second
   unauthorized order.
4. Resolve the ledger attempt(s) so the global unresolved-attempt interlock
   is released. If any order is left unresolved, the interlock **stays
   engaged** and that is reported, not worked around.
5. Final read-back of open orders, positions and balance, retained beside the
   pre-image.

## What authorization is being asked for

- Permission to send **two preprod orders carrying one `clordId`**, on a
  named preprod instrument, at minimum size, post-only, far from the touch.
- Nothing else. No production order, no budget change, no relaxation of the
  single-send rule — the rule can only be revisited *after* this test
  classifies, and then only by a separate decision.

## What is NOT being asked for, and must not be inferred

- Not authorization to retry the historical `CDHM9PJV16R7` send.
- Not authorization to send any order to production.
- Not a conclusion that the preprod result transfers to production. It is
  evidence about the preprod gateway; whether production behaves the same way
  is a separate, unverified claim.
