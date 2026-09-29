# Five management defects, reproduced against the released build aca3564

**Test file:** `backend/tests/test_xavier_management_defects_reproduced_through_the_cycle.py`.
**Run against:** a worktree at `aca3564`, on a database cloned from `mp2` (migrated to 141).
**Result:** 6 failed, 0 passed.
**How it was run:** each test drives `ext_pinnacle_loop.cycle(conn)`, or the production supplier and the pass it calls. Only the venue transport (`pmus._get_client`, and the rules prose), the book-currency seam and the clock are substituted. Market data is synthetic.

| # | defect | test | the failure on aca3564 (verbatim assertion) |
|---|---|---|---|
| 1 | A winning REDUCE cannot be dispatched | `test_a_winning_reduce_is_sent_as_its_bound_plan` | The ledger records `REDUCE`. The pass's exit step is empty, with refusal `THE_PLAN_IS_NOT_THE_ONE_THAT_WAS_RANKED`. The selector's digest-less REDUCE won the stable sort. |
| 2 | DIRECT_EXIT is dropped silently, and management blockers are lost | `test_every_management_alternative_reaches_the_persisted_decision` | The persisted `ranked` set is `{HOLD, REDUCE}` and `unrankable` holds only `TAKE_COMPLEMENT`. There is no DIRECT_EXIT, and none of HOLD_TO_SETTLEMENT, POST_COMPLEMENT or MERGE. |
| 3 | The hedge valuation's held-leg basis differs from HOLD's | `test_the_hedge_valuation_uses_the_basis_hold_uses[LONG]` | The leg costs `62` cents (the entry's limit) against a fill basis of `58`. |
| 3 | (short side) | `test_the_hedge_valuation_uses_the_basis_hold_uses[SHORT]` | The leg costs `40` cents (the YES wire price, unconverted) against a short-side basis of `62`. |
| 4 | An acquired hedge leg has no payout event | `test_an_acquired_hedge_leg_is_manageable` | The HEDGE intent has `payout_event: None`, so `select_exit` would refuse it every cycle with R_NO_PAYOUT_EVENT. |
| 5 | The decision is not bound to the order | `test_the_order_names_the_decision_and_the_filled_quantity_is_bound` | `bound_filled_qty` is None on the scheduled decision (`pass_once` read a column the intents table does not have). The exit intent's `decision_ref` names no decision id and no plan digest. |

## Why the earlier acceptance tests missed each

- **Defect 1:** the binding tests bound hand-built candidates that already carried a plan digest (`test_the_decision_to_execution_boundary_holds.py`, `test_autonomy_completion_boundaries.py`). The production supplier's second, digest-less REDUCE never existed in them.
- **Defects 2 and 4:** the pair-cycle tests call `pass_once` with a hand-built supplier result: a finished `hold_ranking`, finished plans, and injected region probabilities (`test_the_scheduled_pair_lifecycle.py` `_pair_facts`). The production supplier `funded_pair_inputs` was never on their path. Their hedge acquisition then asserted the order, not the hedge intent's `payout_event`, and never ran management on the hedge leg.
- **Defect 3:** every position in the suite filled exactly at its limit and was LONG. A basis read from the limit, with no side conversion, therefore could not differ from the fill basis.
- **Defect 5:** the scheduled-path test (`test_the_funded_lifecycle_is_complete.py`) asserted only the in-memory dispatch result. It never read the persisted ledger row's `bound_filled_qty`, or the exit intent's `decision_ref`.

## Scope note on defect 4

On aca3564, no hedge can be acquired on the full scheduled path at all. `funded_pair_inputs` supplies no `outside_split`, so every indirect candidate refuses R_NO_OUTSIDE_SPLIT (the D2 defect), and defect 4 is latent behind that one. It is therefore reproduced through `pass_once` with the supplier contract production returns for the hedge record (`hedge_decision_record=None`), followed by the production `manage`. The full-cycle proof of acquiring through `cycle()` and then managing the hedge leg in the next cycle belongs to the integrated Xavier acceptance tests.

## Status

These six tests go on the critical gate list. They must pass on the integrated SHA before release.
