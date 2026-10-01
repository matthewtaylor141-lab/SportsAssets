# PINNACLE_ONLY_PAPER_BENCHMARK: result

**Paper only. This is experimental execution. It is not evidence of qualified or proven profitability.**

- Branch: `claude/rel-bench`, based on `eef233b` (the deployed release).
- Code and test sha: `662bcd1`. This document is committed on top of it as `backend/docs/pinnacle_benchmark_result.md`. The final branch sha is given in the hand-off message.
- `research/pinnacle_benchmark_readback.sql` is also on `claude/command-center` at `11123d1`.

## Files
| file | change |
|---|---|
| `backend/migrations/182_paper_strategy_key_and_pinnacle_only_benchmark.sql` (+ `rollback/…down.sql`) | Adds a `strategy` column (default `DEREK_ENTRY_POLICY_V2`, with a CHECK) on `paper_decisions`, `paper_orders`, `paper_handoffs`, `paper_fills` and `paper_xavier_reviews`. Replaces the unique index `paper_decisions_one_per_valuation_idx` with one on `(session_id, valuation_id, strategy)`. Adds paper_control row `PINNACLE_ONLY_PAPER_BENCHMARK`, enabled, as the kill switch. Adds paper_control row `PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2`, disabled. The file has no BEGIN/COMMIT of its own. The down script refuses to run while benchmark rows exist. |
| `backend/sportsassets/agents/paper_benchmark.py` (new) | The decision logic, the pass step, the hook decision, Xavier's measure for benchmark positions, Audrey's report section and fill audit |
| `agents/paper_runtime.py` | Adds a `benchmark` step only when `PAPER_BENCHMARK` is on. `decide_valuation` runs the benchmark after Derek. |
| `agents/paper_derek.py` | Candidates are filtered by its own strategy. Per-strategy entry switch: a would-be ENTER becomes `STRATEGY_ENTRIES_DISABLED`. |
| `agents/paper_xavier.py` | Handoffs and reviews carry the strategy. Management orders take the group's strategy. Benchmark groups use the Pinnacle-only measure. |
| `agents/paper_audrey.py` | Decision rows are labelled with their strategy. The benchmark gets its own section in the daily report and a fill-audit finding. |
| `bettor_paper_ledger.py` | `submit_order` writes the strategy. Fills take the order's strategy. Positions carry the strategy. |
| `bettor_paper_readmodel.py`, `api/command_paper.py` | Derek decisions now include `strategy` and `explanation`. Positions include `strategy`. New `GET /api/command/paper/benchmark` (decisions, orders, fills, handoffs, ledger, disclosure). |
| `tests/test_pinnacle_only_paper_benchmark.py` (new, 20 tests) | Proofs, listed under Tests |
| `tests/test_calibration_only_records_cannot_trade.py`, `test_command_centre_agent_pages.py`, `test_paper_vertical_slice_derek_to_audrey.py`, `paper_live_fixture.py` | Reader census entry added. Contract fixtures get the new keys. The vertical-slice tests turn the two-model entry switch on for themselves. Switch helpers added. |
| `research/pinnacle_benchmark_readback.sql` | Read-only readback (checked to contain only SELECT statements) |

## Spec item → code → test (all in `test_pinnacle_only_paper_benchmark.py` unless noted)
1. **Label and disclosure.** The strategy label and `DISCLOSURE` are written on the decision (column, label, economics, policy_decision, gaps), the order, the fills, the position, the handoff, the review, the Audrey section and finding, and the payloads. Tests: `test_lifecycle…`, `test_the_constants_and_the_disclosure`.
2. **Probability.** The decision uses the stored `probability` of the event the contract pays on. `contract_match` reuses evaluate's identity and settlement checks unchanged: settlement must be COMPATIBLE, `overall_established`, and carry no lane settlement refusal. It also checks the payout-outcome match and lane probability-stage refusals. Freshness is `paper_derek._pinnacle`, the 30 s rule re-aged to the decision instant. Tests: `test_refusals…` (stale, INCOMPATIBLE, NOT_COMPARED, payout AWAY).
3. **Edge, EV and price.**
   - Every level used must have at least 5.0 pp of edge (`size_within_edge`: the limit is the deepest level that still clears).
   - EV after fees must be above 0. Fees come from `L._fee`, the same function the simulator uses. The void rate is applied when it has been measured.
   - The price comes from a current book observation: our receipt time, at most 10 s old. Only displayed depth counts, net of consumed liquidity. PAPER_SIM_V1 has no haircut parameter.
   - Fills happen 2 s after the decision, under the simulator's own rules.
   - Tests: `test_every_level_used_clears_5pp_and_4_9pp_does_not`, `test_refusals…` (4.9 pp, no book), `test_ev_not_positive…` (−$10).
4. **P5.** `BOOK_CURRENCY` is recorded as `NOT_ESTABLISHED`, with mechanism and basis, on the book, the economics, the policy_decision and the order label. Funded code and the P5 rule are untouched. Test: lifecycle.
5. **No internal model.** `p_internal` is NULL, `internal_model` is `{available:false, reason}`, `p_blended` is NULL. Tests: lifecycle, refusals.
6. **Same account, session and rails.** Orders go through `L.submit_order` with the session's `risk` caps. There is no new funding and no new session. Tests: `_ledger_matches_balances` (exactly one INITIAL_FUNDING) in every pg test.
7. **Xavier and Audrey.** Benchmark fills reach `step_handoff`, the Xavier review (`xavier_measure`), the Audrey report section and `audit_fills`. Tests: lifecycle, `test_both_strategies…`.
8. **Two strategies side by side.** Migration 182 and strategy-filtered candidates let both strategies decide the same valuation. Tests: lifecycle (2 decisions), `test_both_strategies…`.
9. **Switch.** Benchmark requires the env var `PAPER_BENCHMARK` (on/1/true/yes) and its control row. With the env var unset there is no step and no hook call. Tests: `test_switch_off_writes_no_benchmark_rows` (also checks the row-off case and that the hook returns the same keys as before), `test_switch_reads_the_environment`.
10. **Isolation.** Tests:
    - `test_the_benchmark_imports_only_paper_and_pure_policy_modules`
    - `test_importing_the_benchmark_loads_no_funded_or_submission_module` (subprocess)
    - `test_no_funded_module_reaches_the_benchmark`
    - `test_no_paper_module_imports_a_submission_path`
    - existing `test_paper_records_cannot_reach_the_funded_path.py`
    - `mutation_attempts == 0` is asserted in lifecycle and in both-strategies.
11. **Refusal records and readback.** Refusals are recorded with `economics.shortfall` (edge_pp vs 5.0, EV, depth, Pinnacle and book ages). The readback SQL ran with no errors (psql `ON_ERROR_STOP`) on the scratch DB. Tests: `test_refusals…`, `test_readback_sql_is_read_only`.

**Owner additions**
- **Keys are per strategy.** Every key derives from `paperbench:<sha(session:valuation:STRATEGY)>`. Test: `test_every_key_is_strategy_specific`.
- **One ledger, no over-commitment.** Test: `test_both_strategies_reserving_concurrently_never_overcommit` (2 connections, $1,000 available, 6 rounds: one order reserves, the other is refused INSUFFICIENT).
- **Two-model entries off.** Two-model decisions are still recorded, but a would-be ENTER is recorded as REFUSE `STRATEGY_ENTRIES_DISABLED`; `policy_decision.admitted` stays true. Test: `test_two_model_entries_switch_off…`.
- **4a, repeats and restarts.** Test: `test_repeated_valuations_and_restarts_duplicate_nothing` (hook then backstop on the same valuation; restart after submit; restart after fill and before handoff).
- **4b, fills reach Xavier.** Tests: lifecycle and both-strategies (handoff qty = position qty = filled qty, with the strategy label).
- **4c, reconciliation.** Test: `test_both_strategies_purchases_sales_settlements_reconcile`. Per strategy, the ledger shows ORDER_SUBMITTED, FILL, SALE and SETTLEMENT. Total cash = sum of ledger, reserved = 0, `ledger_consistent`, and Audrey's report reconciles.
- **4d.** Covered by the isolation tests under item 10.

## Tests (verbatim)
Setup: scratch DB `rb_final` created from template `mp2`, then
`DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/rb_final python -m sportsassets.scripts.migrate`.

```
cd backend; RN1X_TEST_DSN=postgresql://postgres:postgres@127.0.0.1:5432/rb_final \
  nice -n 10 /tmp/claude-0/gatevenv312/bin/python -m pytest -q -p no:cacheprovider \
  $(ls tests | grep -iE '^test.*(paper|derek|xavier|audrey|command_centre)' | sed 's|^|tests/|') \
  tests/test_calibration_only_records_cannot_trade.py
461 passed, 4 warnings in 222.49s        (41 files + the census file)

pytest -q tests/test_pinnacle_only_paper_benchmark.py
20 passed, 1 warning in 5.22s
```
- **Before the change.** At eef233b, the paper/derek/xavier/audrey files alone gave `339 passed`.
- **Flaky test.** In one run on the reused DB, `test_xavier_tradeoff_directive_becomes_an_evaluated_artifact.py::test_a_loss_directive…` failed once. Run on its own it passes: 6 passed at both head and base. The clean run above includes it.
- **The 31 test files that read the migrations directory.** I ran them at eef233b (DB `rb_base`) and at head. Both give `22 failed, 831 passed` with the same failing set, so these failures existed before the change. For example, `test_c10…` expects the newest migration to be `064_`.

## Operator notes
- **To enable:** set `PAPER_BENCHMARK=on`. This also needs `PAPER_SESSION=on` and both control rows enabled (the benchmark row is enabled by default).
- **Kill switch:** set `paper_control` row `PINNACLE_ONLY_PAPER_BENCHMARK` to `enabled=false`.
- **Two-model entries** stay off until someone enables `PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2`.
- **Calibration:** the benchmark does not read the Pinnacle source's calibration. It does not import the collection worker, and the gap is recorded as `NOT_CLAIMED`.
- **With the env var unset,** decisions, orders and the ledger behave as before, with three exceptions:
  - API payloads gain the `strategy` and `explanation` fields.
  - Audrey's decision rows name their strategy.
  - The two-model entry switch is off. This only matters once a research model exists; today the two-model strategy refuses every decision anyway.
- Scratch DBs were dropped afterwards.
