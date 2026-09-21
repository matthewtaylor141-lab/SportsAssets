# BETTOR RELEASE CANDIDATE — integrated engine, real-data results

Restrictions unchanged: `mirror_live=false`, **no real orders, no capital
activation, nothing deployed**. Production remains `3349219`.

**178 tests pass.** Three commands, all exit 0, all outputs committed.

| # | command | artifact |
|---|---|---|
| 1 | `python3 scripts/bettor_shadow_demo.py` | `shadow_demo_output.txt` |
| 2 | `python3 scripts/bettor_replay_real.py` | `replay_real_output.txt` |
| 3 | `python3 scripts/bettor_prospective_runner.py --self-test` | `prospective_selftest_output.txt` |

---

## 1. THE INTEGRATED ENGINE

| component | file | status |
|---|---|---|
| Decision engine | `backend/sportsassets/bettor_decision_engine.py` | working |
| Venue/account contract | `backend/sportsassets/bettor_venue_contract.py` | working |
| Maker economics | `backend/sportsassets/bettor_maker_economics.py` | working |
| Observation adapter | `backend/sportsassets/bettor_observation_adapter.py` | working |
| Shadow loop | `backend/sportsassets/bettor_shadow_loop.py` | working |
| Prospective runner | `scripts/bettor_prospective_runner.py` | working, **not deployed** |

Chain: `observation → normalize → decide → simulated execution → inventory →
settlement → reconciled accounting`. `ShadowAdapter` is a different class from
the production adapter, not the same one in a mode; the prospective runner
imports neither it nor any venue client, asserted by AST scan.

## 2. SYNTHETIC REGRESSION DEMONSTRATION — command 1

Nine sections, each asserting **expected cash and expected inventory
independently** of the ledger's own reconciliation — necessary because the
ledger was self-consistent while fees were being dropped entirely.

Covers: non-zero-fee entry (89.90, fees 0.40); partial sale through the engine
(basis removed proportionally, 4.90 → 2.94); complete sale (**zero remaining
basis**); one-leg recovery across all four branches (COMPLETE / EXIT /
UNRESOLVED-no-fresh-read / UNRESOLVED-no-action); quote lifecycle
(rest → touch → reprice → fill → cancel); settlement; restart preserving
balances, inventory, quotes and provenance; the maker counterexample; and
ledger reconciliation with **residual 0**.

## 3. REAL-OBSERVATION REPLAY — command 2

36 real rows from `bettor_state_observations`, under
`('polymarket-us','institutional')` — `holds_both_legs_independently =
UNKNOWN`. The demo fixture is not used.

**Normalization:** 15 accepted, 21 rejected. Ages 5.776s – 27.985s, median
11.495s, bound 10s. **Every rejection is staleness alone** — the books are
readable, open and two-sided, so age is the whole filter (asserted by test).

| bound | rows surviving |
|---|---|
| ≤ 10s | 15 of 36 |
| ≤ 30s | 36 of 36 |

Shown so the eligibility curve is visible, **not** to justify relaxing the
bound.

**Pairing:** 0 genuine complement pairs in this sample. Venue-wide, **11
contracts** have two distinct outcome legs observed — so pairs exist, just not
among the freshest 36.

**Decisions:** 15 of 15 → `NO_TRADE`. Blockers:
`FV_BETTOR_INDEPENDENT_NOT_VALIDATED` ×60, `P_FILL_NOT_IDENTIFIED` ×30,
`COMPLEMENT_ABSENT` ×15, `NO_PAIRED_INVENTORY` ×15.

**Execution:** 0 orders. Ledger residual 0, fees 0, basis 0, positions 0.
Every action is blocked or unidentified on this venue contract;
**manufacturing a fill to produce a number is precisely what this run must
not do.**

**Settlement:** `bettor_state_settlements` holds **0 rows venue-wide**. No
outcome has matured for any observation. This is the "fresh outcomes have not
matured" case, stated plainly.

## 4. PROSPECTIVE RUNNER — command 3

**Status: PREPARED AND SELF-TESTED, NOT RUNNING.**

Self-test passes: decided 15, duplicates refused 36, rejected 21. Dedup and
restart both verified, and the no-order-path check is an AST scan of calls and
imports rather than a substring scan (which failed on its own forbidden-name
list).

Dedup is **not** an efficiency measure: re-deciding an observation after its
market moved would silently convert a prospective record into a hindsight one.

**The one remaining action to make it live** — and it needs approval, so I have
not taken it:

> Deploy `scripts/bettor_prospective_runner.py` on `sportsassets-workers` as a
> decision-only loop reading the live PMUS book, writing
> `PROSPECTIVE_SHADOW` records. **Rollback:** stop the process; it holds no
> state outside its own JSON file and touches no trading path.

**It would currently produce `NO_TRADE` on every row**, for the reasons in §3.
Running it establishes the record-keeping, not an edge.

## 5. STRATEGY EVIDENCE

Frozen **before** any outcome was read:
[`PREREGISTRATION_MAKER_V1.md`](PREREGISTRATION_MAKER_V1.md).

Maker round-trip on the 15 real books, across the frozen grid, per contract:

| mid move | exit route | positive | negative | median |
|---|---|---|---|---|
| 0.000 | cross out | 0 | 15 | −0.010000 |
| 0.000 | rest out | 12 | 3 | +0.000000 |
| −0.005 | cross out | 0 | 15 | −0.015000 |
| −0.005 | rest out | 7 | 8 | −0.005000 |
| −0.010 | cross out | 0 | 15 | −0.020000 |
| −0.020 | cross out | 0 | 15 | −0.030000 |
| **−0.020** | **rest out** | **6** | **9** | **−0.020000** |

### Verdicts under the frozen rules

| candidate | verdict |
|---|---|
| **M1** (passive quote, held/exited) | **REFUTED for the crossing route** — negative on 15/15 at every grid point, including zero adverse move, where the hypothetical maker fee alone makes it −0.010. **UNRESOLVED for the resting route** — fails rule 1 (positive at *every* grid point: only 6/15 at the conservative corner) and fails rule 2 (needs `p_fill` and `conditional_reference_move`, both NOT_IDENTIFIED). |
| **M2** (maker first leg + completion) | **UNRESOLVED — BLOCKED ON CAPABILITY.** `holds_both_legs_independently` is UNKNOWN on the institutional account. Not evaluable until resolved. |
| **I1** (completion-vs-exit policy) | **SUPPORTED AS A POLICY, NOT AN EDGE.** The rule selects the higher-incremental-cash action on every constructed case, and excludes sunk basis (asserted: bases of 0.10 and 0.90 give identical choices). This is a correctness property, not evidence of profitability. |

**No candidate is SUPPORTED as an edge.** Sample is 15 contracts against a
required 200; the interval test was not reached. Acceptance rules were not
relaxed.

### Data, assumptions, uncertainty

- **Data:** 36 real observations, 15 decision-eligible, 0 settled.
- **Assumed:** the entire fee schedule (`HYPOTHETICAL_NO_VERIFIED_SCHEDULE_EXISTS`),
  every grid value, and **every fill** — BETTOR has never rested an order.
- **Uncertainty:** three of the four terms M1 needs are NOT_IDENTIFIED, as
  recorded in the pre-registration *before* these numbers existed.

## 6. CAPACITY ANALYSIS — $500,000/day

### Technical throughput (software)

Decision path measured at **~15,000 observations/second** single-threaded
(15 rows through normalize+decide in under 1 ms). At 10 contracts × $0.50,
sustaining $500k/day needs ~100,000 contracts/day ≈ **1.2 contracts/second**.
**The software is not the constraint — it is roughly four orders of magnitude
clear.**

### Economically eligible turnover (observations)

| | |
|---|---|
| Distinct markets observed/day | 810 (2026-09-21), 570 (2026-09-20) |
| Decision-eligible at the 10s bound | **42%** of usable rows (15/36) |
| Eligible opportunities with positive identified EV | **0** |
| **Economically supportable turnover today** | **$0** |

The shortfall is not a gap to be narrowed by scaling. **It is total**, because
no action currently has an identified positive EV, and trading negative-EV
opportunities to manufacture turnover is explicitly excluded.

### What would have to change, quantified

To reach $500k/day at ~$0.50/contract — 100,000 contracts/day, ~1.2/sec —
**all four** of the following are required:

1. **A positive-EV action must exist.** None does. Requires either `p_fill`
   measured (needs our own resting orders) or `holds_both_legs_independently`
   resolved SUPPORTED with observed complements.
2. **Depth data.** The capture schema has **no size column**, so no order can
   be sized. 100,000 contracts/day across 810 markets is ~123 contracts per
   market per day; whether that depth exists is **unmeasured**.
3. **A decision-latency feed.** 58% of even the freshest rows exceed a 10s
   bound. The research sampler reads at 60/300/900/3600s horizons — it was
   never built for this.
4. **Capital.** At ~$0.50/contract and same-day settlement, 100,000
   contracts/day needs **~$50,000 of working capital per turn cycle**;
   settlement timing is unmeasured, so the multiplier on that is unknown.

**Uncertainty:** items 2, 3 and 4 are unmeasured, not estimated. Item 1 is
measured and currently negative.

## 7. REMAINING REQUIREMENTS FOR A BOUNDED LIVE PILOT

| # | requirement | status |
|---|---|---|
| 1 | Verified fee schedule from a settled statement | **missing** — everything is hypothetical |
| 2 | `holds_both_legs_independently` resolved for the institutional account | **UNKNOWN** — `reconcile_read.capability_probe()` is built and unrun |
| 3 | Account identity verified | **blocked** — no identifier on the snapshot |
| 4 | Depth in the capture schema | **missing** — no size column exists |
| 5 | Decision-latency read path | **missing** — research sampler only |
| 6 | `p_fill` measured | **impossible without a pilot** — requires our own resting orders |
| 7 | Execution gate deployed | built, tested, **undeployed** |
| 8 | A candidate SUPPORTED under frozen rules | **none** |

**Items 1–5 and 7 are engineering and can be done without trading. Item 6 is
genuinely circular** — measuring fill probability requires resting orders, and
resting orders require a pilot. That circularity is the real gate on this
mandate, and the smallest honest break is a **minimum-size, loss-capped maker
pilot whose sole deliverable is a `p_fill` measurement**, not profit.

I have not proposed its parameters here because it needs capital
authorization, which is outside what I hold.

## 8. WHAT IS AND IS NOT ESTABLISHED

**Established:** the engine consumes real venue data, normalizes it under
institutional semantics, evaluates every alternative with explicit economics,
manages simulated execution and inventory, reconciles cash to residual 0, and
refuses every action it cannot justify — on real data, not fixtures.

**Not established:** any edge, any fill rate, any profitability, and any
capacity above zero. Class C stays FALSIFIED within its tested scope; Class D
stays LOCKED; directional stays blocked on a measurement whose interval
crosses zero and which therefore refutes nothing either.
