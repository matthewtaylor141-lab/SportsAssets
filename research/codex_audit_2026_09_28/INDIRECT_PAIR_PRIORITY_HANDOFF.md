# Indirect pairs: priority repair and the remaining funded path

Date: 2026-09-28. Reviewed base: `12dcb2a`.

## Owner requirement

Prioritize indirect middles: retain an existing holding and acquire a distinct
contract on the same fixture whose payout overlaps it, so both can win and the
verified settlement states do not permit both to lose. Direct same-contract
netting is not a substitute for this strategy.

This is a search and development priority, not permission to buy every middle.
Selection must compare holding, exiting, reducing and acquiring the hedge on a
consistent forward-value basis, including fees, liquidity, capital committed,
time to release and settlement uncertainty. Retain total cost-basis P&L for
reporting. Do not substitute historical entry cost for today's opportunity cost.

For an illustrative equal-unit Bears moneyline / Panthers +4.5 pair, with
compatible full-game rules, the ordinary non-tie outcomes are:

| Outcome | Moneyline payout | Spread payout | Total |
|---|---:|---:|---:|
| Bears lose | $0 | $1 | $1 |
| Bears win by 1-4 | $1 | $1 | $2 |
| Bears win by 5+ | $1 | $0 | $1 |

Tie, void, push and postponement treatment must come from the actual contracts.
This table does not assert that any particular historical game landed in the
middle. A $1 floor does not imply a positive return when cost plus fees exceeds
$1. Better return on capital is a hypothesis to measure prospectively, not a
property of the word MIDDLE.

## Repairs in this patch

1. **An unequal-size middle lost the declared preference.** Reproduction: held
   moneyline 10, indirect spread 5, direct complement 10. The allocator labelled
   the indirect relationship PARTIAL and sorted it after DIRECT_COMPLEMENT,
   allocating all 10 held units to the direct pair. The patch ranks the matched
   units' payoff shape, after validating original quantities. It now allocates
   5 to MIDDLE first, then the remaining 5 to the direct structure. No inventory
   is duplicated. This allocator describes holdings; it does not place orders.
2. **An integer-line push became a B-side win.** Captured prose alone was enough
   to pass validation, despite there being no push-payout field. The raw grader
   treated equality as a win for B. Integer lines now refuse classification
   until an explicit push-payout representation is implemented, and the grader
   returns unknown at equality. Half-point middles remain supported.
3. **Refund prose became an invented 50-cent payout.** A rule saying "refund
   stake" was converted to 50 cents, even for an 80-cent stake. That inference
   is removed. The void state remains unestablished without a supported explicit
   payout rule. This is not an implementation of stake-refund accounting.

## Verification

New tests were run before each repair: 18 preference cases failed on the
original allocator; both settlement counterexamples failed before containment.
After repair: **141 passed, zero skipped**, across:

- `test_indirect_pair_priority.py` (28 tests, including all six input orderings,
  smaller/equal/larger quantities, conservation, explicit preference override,
  incompatible periods and settlement counterexamples)
- `test_indirect_structures.py`
- `test_the_pairing_panel_never_sums_the_books.py`
- `test_learning_cannot_change_its_own_gates.py`

These are local component tests, not PostgreSQL acceptance, production
verification or funded execution evidence. No control, credential, deployment,
protected worker or order was changed.

## The missing production connection, confirmed in code

- `bettor_funded_management.EXECUTABLE_ACTIONS` contains only DIRECT_EXIT and
  REDUCE. FORM_INDIRECT_HEDGE has no funded dispatch.
- `bettor_ev_actions` records indirect EV and position state as NOT_REPRESENTED;
  `P_MIDDLE_LANDS` is an unmet requirement, not a fitted funded model.
- Migration 126 creates `bettor_funded_one_open_position` on the constant
  expression `(true)` for open ENTRY rows. It permits only ONE open ENTRY
  globally in this table. An indirect acquisition cannot simply reuse the entry
  path while the first holding is open. There is no later replacement of this
  index in the reviewed migrations.
- The allocation repair above changes none of these facts. Same-contract PMUS
  opposite-side trading nets the original instrument; it does not create the
  second independently settling holding required here.

## Completion work for Claude

Apply/review this patch on top of the current branch and continue the transport
repair independently. Do not mark indirect pairing executable after applying it.

1. Supply candidates from the actual held position and venue catalogue: same
   fixture, correctly oriented distinct contract, compatible period/rules,
   current executable prices and depth. Do not require a second venue merely
   because there are two contracts. A combo/parlay is not a substitute for two
   separately paying holdings.
2. Build joint margin/outcome probabilities, with provenance and calibration;
   do not multiply correlated legs as independent events. Rank the proposed
   portfolio versus HOLD/EXIT/REDUCE using the same valuation convention and
   incremental capital. A structural preference cannot manufacture missing EV.
3. Represent a bounded portfolio group and acquire a second leg through a real
   funded planner/adapter path. Replace the incompatible single-entry invariant
   with an equally explicit group/leg invariant and transactional reservations.
   Do not simply remove the unique index. Acquiring the hedge spends capital and
   must not bypass entry/exposure authority by labelling the order an exit.
4. Reserve existing inventory once; treat only filled matched units as paired.
   Preserve pending/ambiguous acquisition exposure across restart. Demonstrate
   partial fill, lost acknowledgement, duplicate execution and a vanished hedge
   opportunity without double allocation or resend.
5. Reconcile both contracts independently and the group jointly, including fees,
   partial realized P&L, residuals and remaining basis. Persist the selected
   action, pair identifier, payoff floor, middle probability and rejection reason
   through the real scheduled heartbeat and Command Centre.
6. Feed versioned pre-trade predictions and subsequent fills/results back into
   an isolated learner. Compare a challenger on unseen chronological outcomes
   and net economics before bounded promotion. Keep authorization, account
   eligibility, limits and evidence requirements outside learner write authority.
   A retraining job or profitable retrospective sample is not proof of improvement.

Acceptance must follow the authorized API scheduler through acquisition,
reservation, adapter payload, partial fill, restart, reconciliation and operator
readback with PostgreSQL tests actually running. Full release evidence must be
on the exact deployed SHA. The protected worker remains outside this work.
