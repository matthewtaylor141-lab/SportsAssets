# RUN 83 — DEPLOYMENT-READINESS PACKET

**NOT DEPLOYED. NOT COLLECTING. Awaiting explicit approval.**

---

## 1. GIT COMMIT

```
a14d400673bc7d34b6d3521e67f33dfff8c7d040   branch claude/session-njaewf
```

Every run 83 commit carries `[skip render]`, so none has deployed.

## 2. FULL-SUITE RESULT

| | HEAD `a14d400` | baseline `8dd4e6e` (pre-run-83) |
|---|---|---|
| total collected | 7,802 | 7,741 |
| **passed** | **7,679** | 7,529 |
| **failed** | **21** | **110** |
| skipped | 99 | 99 |
| xfailed | 3 | 3 |
| duration | 5m49s | 7m12s |

Same suite, same ordering (`-p no:randomly`), same machine.

**Failures at HEAD that are absent at baseline: NONE.** Every one of the 21 is a
member of the baseline's 110.

### The exact 21

- `tests/test_c8_tennis_witness.py::test_c8_a_candidate_with_a_fill_inside_the_day_is_read`
- `tests/test_e19_smaller_reading.py::test_e19_the_fills_vs_venue_preset_carries_both_keys_and_the_venues_figures_in_case_order`
- `tests/test_fill_c9_record_cols.py::test_c9_the_presets_read_the_columns_guarded_and_the_hourly_is_still_nine`
- `tests/test_mirror_live_worker.py::test_the_cap_is_per_game_a_second_market_is_scaled_to_what_the_game_has_left`
- `tests/test_mirror_live_worker.py::test_a_short_books_collateral_counts_against_its_game`
- `tests/test_mirror_live_worker.py::test_a_resting_buy_counts_its_unfilled_notional_against_the_game`
- `tests/test_mirror_live_worker.py::test_the_cap_is_per_event_two_whales_books_of_one_game_share_it`
- `tests/test_mirror_live_worker.py::test_the_walk_order_is_games_oldest_touched_first_and_book_id_within_a_game`
- `tests/test_mirror_live_worker.py::test_a_candidate_on_a_full_game_opens_nothing_and_one_on_a_part_full_game_opens_scaled`
- `tests/test_mirror_live_worker.py::test_the_game_room_is_applied_at_cost_so_a_fallen_mark_never_averages_down_past_the_cap`
- `tests/test_mirror_live_worker.py::test_a_full_games_unopened_markets_are_skipped_for_the_memo_then_read_again`
- `tests/test_mirror_live_worker.py::test_two_candidates_on_one_game_in_one_tick_share_the_room`
- `tests/test_mirror_live_worker.py::test_a_short_books_increase_under_a_bound_room_is_sized_in_collateral`
- `tests/test_mirror_live_worker.py::test_the_woken_game_starts_first_and_a_games_books_stay_in_id_order`
- `tests/test_mirror_live_worker.py::test_r_take_cycles_burn_the_entry_budget_but_an_exit_or_a_side_change_is_never_gated`
- `tests/test_mirror_live_worker.py::test_w2_one_row_per_transition_and_a_restamp_after_900_s`
- `tests/test_mirror_live_worker.py::test_t1_the_turn_names_are_emitted_here_too`
- `tests/test_pmus_account.py::test_a_position_we_sold_is_settled_on_the_day_of_the_sale`
- `tests/test_pnl_l34_review_pins.py::test_r_defect_h1_the_venue_fresher_than_the_fills_is_refused_drift_not_sized`
- `tests/test_pnl_l34_review_pins.py::test_r_defect_m1_the_deploy_seed_admits_the_landed_rules_unwitnessed_reduce`
- `tests/test_pnl_l34_review_pins.py::test_r_the_fast_tick_explains_the_walks_drift_the_same_way`

### Do they touch any run 83 code path?

**No.** None of the six files contains a single reference to the `obs` package
(checked by grep: 0 hits in each). None imports `sportsassets.obs`, none
exercises the collector, the schema, or the receipt hook.

### Did they pre-exist?

**Yes — all 21.** Proved by the set comparison above against a full-suite run at
the pre-run-83 commit in a separate worktree.

### Do they depend on ordering or load?

**Yes, and this is the mechanism, named rather than waved at.**
`test_mirror_live_worker.py` contributes 14 of the 21 and **passes 367/367 when
run alone** at HEAD. It is not a single poisoning predecessor either: pairing it
with each of `test_e7_cand_memo.py`, `test_fill_t1_turn.py` and
`test_e38_two_legged.py` passes in every case. The effect therefore requires a
**long cumulative prefix** of the suite — state that accumulates across many
files, not a pairwise interaction.

The same run-to-run instability is visible in the totals on essentially unchanged
code: **110 → 42 → 26 → 21** failures across four runs today. That non-determinism
is task #124's open work and predates run 83; I have **not** isolated the exact
accumulating object, and I am not calling it "flaky" as a substitute for that.
What is established is narrower and sufficient for this gate: **the failures
pre-exist, they are absent from run 83's code paths, and run 83 removed 89 of
them rather than adding any.**

### Does any failure affect the passive-safety proof?

**No.** The safety suite is separate and fully green (item 3). None of the 21
touches order-submission isolation, the append-only tables, source identity,
timestamp provenance, collector boot, worker startup or restart behaviour.

### HALT check

Matt's HALT list: order submission isolation · append-only tables · source
identity · timestamp provenance · collector boot · worker startup · restart.
**No remaining failure touches any of them. No HALT.**

> **Correction, stated plainly.** An earlier report said "run 83 adds no failure"
> on the strength of checking three files in isolation. That was wrong: run 83
> added **seventeen** — sixteen "the newest migration is 061" guards, plus one in
> `test_e21_fast_add_replan.py` written as "no file starting 062_ exists". All
> seventeen are fixed; the e21 guard was rewritten to check its actual meaning
> (no migration belongs to *that* wave) so it will not need re-pinning again.

## 3. TARGETED SAFETY-SUITE RESULT

```
tests/test_obs_safety.py               4 passed
tests/test_obs_clock_and_schedule.py  16 passed
tests/test_obs_collector.py           21 passed
tests/test_obs_config_independence.py 20 passed
                                      61 passed
```

## 4. CONFIG INDEPENDENCE PROOF

Two directions, proved separately:

- **Observability runs with trading OFF.** `RN1_OBSERVABILITY_SHADOW=true` +
  `mirror_live=false` is a legal, working configuration. `shadow_enabled()` is
  AST-checked to consult **exactly one** env var — if anything ever makes the
  collector require `mirror_live=true`, the test fails and it does not ship.
  **Fail closed.**
- **Observability cannot turn trading ON.** 12 per-switch read checks across all
  obs modules and the worker; an AST check on the write side (no `setenv`,
  `putenv`, state setter or `os.environ[...] =`); and a **behavioural** check that
  imports the whole package with the flag on and confirms every trading switch is
  byte-for-byte where it was.
- **Dependency direction:** `ingestion/pipeline.py` → `obs`, never the reverse.
  No trading module references `obs.collector` or `obs.record`.

## 5. PASSIVE CALL-GRAPH PROOF

- **Static:** transitive first-party import closure of `sportsassets.obs` must lie
  inside a reviewed **allow-list**. Today the closure is `obs.*` + `sportsassets.db`.
  An allow-list, not a deny-list: a deny-list of order modules goes stale the day
  someone adds an egress.
- **Name-pattern + source-text** backstops for late imports and `getattr`.
- **Dynamic:** the package is imported with every order-shaped callable in the
  tree replaced by a tripwire that fails the run if called.
- The book is read by **plain HTTP GET** — no venue SDK is in the import graph.

## 6. SCHEMA / MIGRATION STATUS

`backend/migrations/062_rn1_observability.sql` — **written, never applied
anywhere.** Four append-only tables. Applied at the **API's** next boot only
(`backend/start.sh`); the workers process never runs migrations. Additive: creates
new tables and triggers, touches no existing table.

## 7. RETENTION / STORAGE PLAN

`research/RUN83_RETENTION_AND_CAPACITY.md`.

- **No deletion**, by construction: no DELETE in the writer, and a trigger raises
  on DELETE *and* UPDATE on all four tables. The retention worker is pinned to
  `ai_trades` + `copy_probes` and re-checks its plan every cycle; a test pins
  `rn1_obs_*` as absent from both objects.
- **Measured rate** from the sealed U0 witness: 11,324 events/day mean over the
  last 14 complete days; peak 15,278; most recent complete day 16,184 and rising.
- **~124 MB/day, ~3.7 GB per 30 days** at a 17,000/day design rate.
- **The binding constraint is not disk.** 10 reads/event at that rate is **1.97
  reads/s** against a **2.0** default pacer — ~98% utilisation, no headroom. Pacing
  misses would cluster in the busiest minutes, which are the minutes with the most
  price movement: a **selection effect on the curve**, not random loss.
  **Recommendation: `RN1_OBSERVABILITY_RPS=3.0` for the first cohort while trading
  is paused, then read the `SKIPPED_PACING` counters before deciding further.**
  The code default stays 2.0 until you say otherwise.

## 8. LEGACY-COMPARABLE OBSERVATION LABEL

Every snapshot carries `observation_channel`, `transport`, `feed_identity`; the
migration **refuses an unnamed channel**.

```
LEGACY_COMPARABLE_BOOK_PATH   raw CLOB /book HTTP read
```

Chosen because it is **the same endpoint U2 was built from**, so the forward curve
is the same quantity as 81A/81B/82 measured. It is **not**
`FASTEST_AVAILABLE_BOOK_PATH` and **not** `EARLIEST_ACTIONABLE_MARKET_STATE`.
`FAST_STREAM_PATH` / `websocket_stream` are **declared only** — nothing implements
a stream and a test asserts the package contains no websocket code.

## 9. SOURCE-CLOCK LIMITATION LANGUAGE

```
SOURCE_TO_RECEIPT_LATENCY = NOT IDENTIFIED      (every lane)
```

`BOUNDED` requires a defensible uncertainty interval spanning **both** domains —
and the bound must be **narrower than the quantity claimed**, or it is `BOUNDED`
in name and `NOT IDENTIFIED` in effect. NTP discipline constrains **our** clock
only; it establishes nothing about the remote clock's offset or error. Two
timestamps rendering as UTC is not synchronisation. Per-lane evidence
requirements: `research/RUN83_FINDINGS.md` §9A.

## 10. ROLLBACK PROCEDURE

| step | action |
|---|---|
| **stop collecting** (instant, no deploy) | unset `RN1_OBSERVABILITY_SHADOW` (or set `false`) and restart the worker. The loop logs one line and returns. Data already collected is untouched. |
| **stop collecting without a restart** | set `RN1_OBSERVABILITY_RPS` very low — degrades but does not stop. Prefer the flag. |
| **revert the code** | `git revert` the run 83 commits, or redeploy any commit at/before `8dd4e6e`. The receipt hook and the `TradeEvent` fields disappear with it; nothing else in ingestion changes, because nothing else was changed. |
| **the migration** | tables remain. They are inert: nothing reads them and nothing writes them once the flag is off. Dropping them is a separate, deliberate migration — **not** part of a rollback, since it would destroy collected evidence. |
| **the `chain.py` fallback ledger** | reverts with the code. Note the *value* written to `trades.ts` is identical with or without it; only the bookkeeping goes away. |
| **blast radius if the collector misbehaves** | it cannot reach an order path (items 4–5), it drops rather than blocks under backpressure, and `observe()` cannot raise into ingestion. The realistic worst case is venue rate-limiting from its own reads — bounded by the pacer, and the mitigation is the flag. |

## 11. EXACT ACTIVATION FLAGS

```
RN1_OBSERVABILITY_SHADOW=true        # the ONLY switch that starts collection
RN1_OBSERVABILITY_RPS=3.0            # recommended for the first cohort (default 2.0)
RN1_OBSERVABILITY_MAX_INFLIGHT=8     # default; events sampled concurrently
CLOB_BASE_URL                        # optional; defaults to the endpoint U2 used
```

Deploying without `RN1_OBSERVABILITY_SHADOW` collects **nothing**.

## 12. TRADING STATE

```
mirror_live REMAINS FALSE.
```

Nothing in run 83 reads it, writes it, or depends on it. `ai_trades` and TRUEEDGE
are untouched. No order was sent, and no code path exists in the collector that
could send one.

---

## THE TWO DECISIONS, STILL SEPARATE

1. **Deploy** — drop `[skip render]`. This restarts the workers, which are
   currently running exits on open positions. Migration 062 creates four empty
   tables at the API's next boot. **Still collects nothing.**
2. **Activate** — set `RN1_OBSERVABILITY_SHADOW=true`.

Awaiting explicit approval for each.
