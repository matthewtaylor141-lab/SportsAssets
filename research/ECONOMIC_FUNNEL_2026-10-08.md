# BETTOR economic funnel, per mechanism and frozen policy: 2026-10-08

Owner directive section 6 (profitability), with the PM request of 02:48Z
(Slack) and the integrator's context of 13:45Z.

**Status.** Research only, read only. Every number below comes from a
`research/*.sql` file run through `research-sql.yml` against the production
database (release 7fd4574e, the same tree as 9b94ef5c / RC4), or from the RC4
evidence packet (pm-acceptance run 37738089957). No row was written, no
authority changed and no venue called. SMALL LIVE = SHADOW, Kalshi live money
is NOT ACTIVATED, Adriana is SHADOW ONLY, and historical PAPER is unchanged.

**Money.** All dollar figures are fictional PAPER dollars from the simulated
account `paper_acct_main` (a $500,000 start). Counterfactual and shadow
figures are labelled `NOT_REALIZED_PNL` and are never added to PAPER P&L.

---

## 0. Verdict

**Is a one-sided 98% lower bound on mean net per independent unit above zero?
NOT ESTABLISHED, for every mechanism.**
**Consistent profitability: UNPROVEN.**

There are two cohorts:

- **The forward cohort** runs under the frozen profitability bind, from the
  2026-10-06 17:38:06Z cutover to 14:02Z on 2026-10-08 (44.4 h). It has
  **zero** qualified PAPER decisions, zero fills and zero settled forward
  trades for every strategy. The 98% bound can't be computed (n = 0).
- **The historical cohort** is every PAPER position ever filled. All of them
  were entered between 2026-10-01 and 2026-10-06 00:47Z, before the bind
  existed. Every mechanism's mean net per independent fixture is **negative**.
  The whole portfolio is significantly negative: its one-sided 98% upper
  bound is still below zero.

### First stopping point per mechanism (forward cohort, unique fixtures)

| Mechanism (evidence class) | Frozen policy | Furthest stage with a non-zero count | First stage at 0 | Cause class | The binding cause, measured |
|---|---|---|---|---|---|
| **DEREK_ENTRY_POLICY_V2** (PROPRIETARY_BETTOR) | DEREK_ENTRY_POLICY_V2 + bind V1 | S5b: executable EV > 0, **3** fixtures | **S5c: all-in EV > 0, 0 fixtures** | **ECONOMIC**. Upstream, 76 of 79 fixtures are lost to **SOFTWARE** (settlement terms). | 546 priced → 79 cleared the policy (5 pp blended edge, net of fees) → **76 capital-ineligible on `CAPITAL_INELIGIBLE_SETTLEMENT_TERMS_UNRESOLVED`** (SOFTWARE / SETTLEMENT) → 3 bound with all-in EV ≤ 0 (`CASH_WAIT_ALL_IN_EXECUTABLE_EV_NOT_POSITIVE`; calibration weight 0.0117, INSUFFICIENT). The lifecycle also refuses all 79: QUARANTINED by LOSS_BUDGET_QUARANTINE at a realized −$10,784.36. |
| **PINNACLE_COMPLETED_GAME_PAPER** (BENCHMARK_PINNACLE_ONLY; not proprietary) | V3 + bind V1 | S5b: **155** fixtures | **S5c: all-in EV > 0, 0 of 155** | **ECONOMIC** (measured non-positive economics) | After the costs measured from the book and the fee schedule, every candidate is positive: +$0.0184 per contract on 194 of 194 contract-sides. The **modelled** costs take that to −$0.209 per contract. The residual haircut alone is −$0.136 per contract, learned from this strategy's realized shortfall. The settled outcomes of the same refused candidates: −$765.15 over 92 fixtures; 98% interval per fixture [−$70.17, +$53.54]. |
| **PINNACLE_EXPLORATION_PAPER** (TRAINING) | V3 | S5b: **153** fixtures entered at decision, with pre-bind executable EV > 0 | **S6: qualified (order placed), 0** | **ECONOMIC** (risk rail on actual realized loss). S5c is **not recorded** (SOFTWARE evidence gap). | All 1,510 ledger-stage entries since the cutover were refused `STRATEGY_LIFECYCLE_QUARANTINED_NO_PAPER_ENTRY`: LOSS_BUDGET_QUARANTINE at −$10,272.81. The bind's verdict on them is computed but never persisted (§12, F1). |
| XAVIER management overlay | migration 206 frozen counterfactuals | 200 FINAL theses / 86 fixtures | n/a (overlay) | **MISSING EVIDENCE**, and a producer coverage gap | Against hold-to-settlement: +$2,097.05, mean +$24.38 per fixture, normal 98% LB −$123.62. Against immediate exit: −$10,643.20. 255 of 471 theses carry no value-add row. |
| ADRIANA structural arbitrage (SHADOW ONLY) | ADRIANA_ARB_ENGINE_V1 | 5,570 structure evaluations | **S5: positive EV, 0 opportunities** | UNCLASSIFIED codes (evaluability) + ECONOMIC | The refusal counts are summed across scans, not per independent unit. 2,506 `CLAIM_LEG_HAS_NO_EVALUABLE_ALIAS`, 1,524 `PAYOFF_FLOOR_BELOW_COST`, 1,166 `STALE_BOOK`, 532 `VOID_TERMS_NOT_ESTABLISHED`. A further 34,094 structure skips are `MONEYLINE_TIE_AND_OVERTIME_TERMS_NOT_MODELLED`. |
| ROUTING savings (counterfactual) | canonical route V1 | 8,828 receipts / 24 events | **comparable route pairs: 0** | UNCLASSIFIED codes. The cause is a single candidate per claim. | Every receipt has exactly one candidate, so no saving against a next-best route is measurable. |
| ALLIE allocation | SHADOW weights | 87,486 shadow rows | S6, by design | No authority, by design | No realized dollar can exist. |

**Champion / CASH state.** CASH is the incumbent and the selected champion.
All three PAPER strategies are QUARANTINED. Leaving QUARANTINED needs a named
person; no automatic promotion exists. Proven positive capacity is **$0**,
recommended capital is **$0**, and ledger cash is $454,258.8638 at seq 2305.

---

## 1. Sources: every number traces to one of these runs

| Step | File | Run ID | Run at (UTC) | Head commit | What it produced |
|---|---|---|---|---|---|
| 0 | `research/ef_discover.sql` | 37787734139 | 13:52:00 | dcddcd48 | Cutover instants, the strategy × policy table, lifecycle, ledger, table sizes |
| 1 | `research/ef_codes.sql` | 37788010927 | 13:54:03 | 2841f46a | Every forward code per strategy; valuations; candidate ledger; refusal census; bind refusals |
| 1b | `research/ef_shapes.sql` | 37788610933 | 13:58:30 | 8b4e883e | JSON key shapes (economics, attribution, labels) |
| 2 | `research/ef_forward_funnel.sql` | 37789135267 | 14:02:18 | 9fb4529b | **Forward funnel** (fixtures, contract-sides), loss stage × class, shared upstream |
| 3 | `research/ef_bind_economics.sql` | 37789410045 | 14:04:14 | cb5d2e02 | **Measured vs modelled cost terms**, bind inputs, hypothetical outcomes |
| 4 | `research/ef_paper_realized.sql` | 37789673595 | 14:06:06 | 085927bb | **Ledger reconciliation**, realized per class / strategy / policy, drawdown, daily, execution, duplicates |
| 1c | `research/ef_codes_all.sql` | 37790226864 | 14:10:12 | 19841171 | Every decision code ever written, per cohort |
| 6 | `research/ef_clusters.sql` | 37790252179 | 14:10:30 | 19841171 | **Cluster values** for the bounds; forward refused candidates; Derek capital-eligibility |
| 5 | `research/ef_mechanisms.sql` | 37790435499 | 14:11:45 | 1fcc7b7c | Xavier value-add, Adriana, routing, Allie |
| 2b | `research/ef_funnel_all.sql` | 37790525934 | 14:12:26 | 1fcc7b7c | **Funnel over every decision, by cohort and frozen policy** |
| 5b | `research/ef_mechanisms_detail.sql` | 37790838063, then 37791068220 | 14:14:44, 14:16:25 | 47024d03, 34a7cd67 | Adriana refusal and skip codes; routing candidates and losers. The second run fixes the nested counters. |
| offline | `research/ef_cluster_bounds.py` over `research/data/ef_*_run37790252179.csv` | none | none | 9d8c7e29 | Student-t (df = n − 1) and seeded bootstrap 98% bounds |
| offline | `research/ef_classify_codes.py` (RC4 taxonomy) | none | none | 9d8c7e29 | Regenerates the code → (stage, class) table in the SQL. The output is identical. |

Two runs produced no result, and nothing is taken from them. Run 37788481798
failed because the file was dispatched before it was pushed. Run 37790239834
was cancelled by the workflow's concurrency group, and run 37790435499 is its
rerun. RC4 packet files cited:
`profitability_scoreboard.json`, `revenue_readiness.json`,
`paper_reconciliation.json`, `completion.json`, `capital_readiness.json` and
`evidence_packet.json`.

### Method

- **Cutover.** The cutover is `schema_migrations.applied_at` of
  `309_paper_profitability_bind.sql`: 2026-10-06 17:38:06.220476Z. This is
  the cutover the production scoreboard itself uses
  (`bettor_paper_profitability_stack.bind_cutover`).
- **Historical cohort.** The last PAPER ENTRY fill in production is
  2026-10-06 00:47:19Z, and no ENTRY order or fill exists after the cutover
  (step 0, §0.6 and §0.7). The historical cohort is therefore the entire
  realized PAPER record. It is shown separately and never presented as
  prospective.
- **Evidence class.** Per the PM: `DEREK_ENTRY_POLICY_V2` is the only policy
  whose probability carries BETTOR's own model, so it is
  **PROPRIETARY_BETTOR**. Its probability is the V2 blend
  (internal + Pinnacle) / 2, and its internal model is itself a calibration
  fitted to market prices (`derek_policy` docstring).
  `PINNACLE_COMPLETED_GAME_PAPER` decides on the de-vigged Pinnacle
  probability alone, so it is **BENCHMARK_PINNACLE_ONLY**. Migration 223
  labels its sleeve INVESTMENT, but it is **not** proprietary profitability.
  `PINNACLE_EXPLORATION_PAPER` is **TRAINING**: negative EV is a research cost
  by design. Shadow, counterfactual and variant rows are **HYPOTHETICAL**.
- **Independent unit.** The independent unit is the **fixture**
  (`paper_fills.fixture` / `paper_decisions.fixture`). All positions of one
  strategy on one fixture form one cluster. The portfolio rows cluster across
  strategies. Repeated evaluations of one contract-side count once: the first
  one, so nothing later leaks in.
- **Funnel stages.**
  - **S1 eligible**: the strategy decided a sealed valuation.
  - **S2 exact mapping**: not lost at PROVIDER, NORMALIZED, MAPPED or
    SETTLEMENT.
  - **S3 fresh inputs**: not lost at MODEL, FAIR_VALUE or BOOK.
  - **S4 fully priced**: reached the EV stage with a walked quantity.
  - **S5a**: the frozen policy's own net-of-fee EV and edge rule admitted the
    candidate.
  - **S5b**: capital-eligibility executable EV > 0.
  - **S5c**: the profitability bind's calibrated all-in EV > 0.
  - **S6 qualified**: a paper ENTRY order was placed.
  - **S7**: simulated fill.
  - **S8**: closed (settled or sold out).
  - **S8s**: closed by a settlement.

  A fixture's stage is the **furthest** stage any of its decisions reached. A
  decision's loss stage and class follow the `coverage_first_loss` census
  rule. The code → (stage, class) table was generated by the RC4 production
  code over every observed code, and an unknown code stays **UNCLASSIFIED**.

---

## 2. Shared upstream (forward window, step 2 §2.5)

| | Count |
|---|---|
| Provider events recorded (ext_candidate_outcomes, 16 sport keys) | **645** |
| … with a venue contract | 564 |
| … with a sealed valuation (external_valuations; every one CALIBRATION_ONLY, step 1 §1.4) | 565 |
| Fixtures decided per strategy (paper_decisions) | **557** |

Of the 645 provider events, 81 never reached a venue contract. The largest
losses by first refusal (step 1 §1.5) were:

- `NO_VENUE_CONTRACT_FOR_EVENT`: 84 provider events, EXTERNAL.
- `PINNAPI_PRIMARY_FEED_HOLDS_NO_FIXTURE_FOR_EITHER_TEAM`: 48, EXTERNAL.
- `PINNAPI_PRIMARY_NO_EXACT_FIXTURE`: 29, SOFTWARE.
- `PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED`: 19, EXTERNAL.
- `VENUE_NATIVE_DISCOVERED_EVENT_NOT_IN_THE_WINDOW`: 19, SOFTWARE.

These counts overlap, because one event can carry several rows. By sport the
largest drops are americanfootball_ncaaf (74 → 53), americanfootball_nfl
(32 → 17) and soccer_usa_mls (39 → 22).

---

## 3. The forward funnel per mechanism and frozen policy (bind cutover → 14:02Z)

Step 2 (run 37789135267), §2.2 counts fixtures and §2.3 counts
contract-sides. The step 2b rerun at 14:12Z (run 37790525934) is consistent:
the Pinnacle-only S5a/S5b moved from 155 to 157 as new valuations arrived,
with S5c still 0.

**Unique fixtures**

| Strategy (class) | Policy | S1 | S2 | S3 | S4 | S5a | S5b | S5c | S6 | S7 | S8 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DEREK (PROPRIETARY) | DEREK_ENTRY_POLICY_V2 | 557 | 557 | 546 | 546 | **79** | **3** | **0** | 0 | 0 | 0 |
| COMPLETED_GAME (BENCHMARK) | V3 | 557 | 557 | 547 | 547 | **155** | **155** | **0** | 0 | 0 | 0 |
| EXPLORATION (TRAINING) | V3 | 557 | 557 | 546 | 546 | **153** | **153** | not recorded | **0** | 0 | 0 |

**Contract-sides**

| Strategy | S1 | S2 | S3 | S4 | S5a | S5b | S5c | S6 |
|---|---|---|---|---|---|---|---|---|
| DEREK | 1,231 | 1,231 | 1,176 | 1,176 | 84 | 3 | 0 | 0 |
| COMPLETED_GAME | 1,231 | 1,231 | 1,182 | 1,182 | 210 | 210 | 0 | 0 |
| EXPLORATION | 1,231 | 1,231 | 1,179 | 1,179 | 208 | 208 | not recorded | 0 |

### Where each fixture stopped (§2.4; furthest decision, with its code and class)

**DEREK** (557 fixtures):

- FAIR_VALUE, SOFTWARE: 11. `FEED_QUOTE_OLDER_THAN_LIMIT` 9,
  `PINNAPI_PRIMARY_INPUT_CHANGED` 1, `PINNAPI_PRIMARY_NO_EXACT_FIXTURE` 1.
- EV, ECONOMIC: 467. `BELOW_MIN_GROSS_EDGE` 465: the blended probability does
  not clear 5 pp at the executable price. `SETTLEMENT_DIFFERENCE_PRICED_VALUE_IS_ZERO` 2.
- ENTER_PASS, ECONOMIC: 79. All 79 were refused by
  `STRATEGY_LIFECYCLE_QUARANTINED_NO_PAPER_ENTRY`. Behind that refusal, step 6
  §6.3 recorded what the capital-eligibility evaluation found:
  - 76 fixtures (81 contract-sides, 756 decisions) were
    `CAPITAL_INELIGIBLE_SETTLEMENT_TERMS_UNRESOLVED`, which is SOFTWARE /
    SETTLEMENT in the RC4 taxonomy.
  - 3 fixtures were capital-eligible, and the bind returned
    `CASH_WAIT_ALL_IN_EXECUTABLE_EV_NOT_POSITIVE`.
  - On the 81 policy-admitted contract-sides, the internal model averaged
    0.7353 against Pinnacle's 0.6272. That is +10.8 pp: the edge Derek's
    policy saw came from the internal model disagreeing with Pinnacle.

**COMPLETED_GAME** (557 fixtures):

- FAIR_VALUE, SOFTWARE: 10.
- EV, ECONOMIC: 392. `BELOW_MIN_GROSS_EDGE` 390,
  `GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT` 2.
- ENTER_PASS, ECONOMIC: 155. Of these:
  - 126 were refused by the lifecycle, with the bind's
    `CASH_WAIT_ALL_IN_EXECUTABLE_EV_NOT_POSITIVE` behind it.
  - 16 were refused by `CASH_WAIT_CALIBRATED_ALL_IN_EV_NOT_POSITIVE`.
  - 13 were refused by `CASH_WAIT_ALL_IN_EXECUTABLE_EV_NOT_POSITIVE`.

  The 29 bind-only refusals fall inside the ~5.9 h after the cutover when the
  strategy still had entry authority: it was ACTIVE_CHALLENGER until 22:26Z
  and REDUCED_SIZE until 23:30Z.

**EXPLORATION** (557 fixtures):

- FAIR_VALUE, SOFTWARE: 11.
- EV, ECONOMIC: 358 (`CASH_WAIT_TOTAL_EXECUTABLE_EV_NOT_POSITIVE`).
- ENTER_PASS: 35. `THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT` 16 (ECONOMIC),
  `XAVIER_HELD_READ_CANNOT_PRICE_THIS_CONTRACT` 14 (SOFTWARE),
  `NO_DEPTH_AT_THE_BEST_LEVEL` 5 (**UNCLASSIFIED**).
- ENTERED at decision: 153. At the ledger, every one was refused by the
  lifecycle QUARANTINE (step 1 §1.7: 1,510 LEDGER refusals over 150 fixtures,
  all with recorded total executable EV > 0).

### Decision-level losses by class (§2.1; volume, not the funnel)

SOFTWARE losses are real but mostly re-decided. A fixture whose first quote
was stale usually gets a later decision that is priced, so SOFTWARE removes
only 10 to 11 fixtures from S3 for each strategy.

| Strategy | SOFTWARE decisions (fixtures) | EXTERNAL | ECONOMIC | UNCLASSIFIED | ENTERED |
|---|---|---|---|---|---|
| DEREK | 3,012 (FAIR_VALUE 2,170 / 305 fx; BOOK 826 / 197; MODEL 14; PROVIDER 2) | 22 | 15,045 | 0 | 0 |
| COMPLETED_GAME | 2,364 | 20 | 15,865 | 0 | 0 |
| EXPLORATION | 2,865 | 22 | 13,556 | 178 | 1,524 |

---

## 4. The funnel per frozen policy over the HISTORICAL cohort (the policies that actually traded)

Step 2b (run 37790525934), §2b.1, unique fixtures. S5b and S5c did not exist
before migration 305 (2026-10-06 05:53Z) and the bind (17:38Z). In this
cohort, S6 is reached from S5a directly.

| Strategy | Policy | S1 | S2 | S3 | S4 | S5a | S6 qualified | S7 filled | S8 closed | S8s settled |
|---|---|---|---|---|---|---|---|---|---|---|
| DEREK (PROPRIETARY) | V2 | 326 | 223 | 179 | 179 | 81 | 53 | 52 | 51 | 29 |
| COMPLETED_GAME (BENCHMARK) | V1 | 14 | 14 | 13 | 13 | 0 | 0 | 0 | 0 | 0 |
| COMPLETED_GAME | V2 | 32 | 32 | 25 | 25 | 4 | 4 | 2 | 2 | 0 |
| COMPLETED_GAME | V3 | 313 | 289 | 266 | 266 | 88 | 46 | 35 | 35 | 15 |
| EXPLORATION (TRAINING) | V1 | 17 | 17 | 16 | 16 | 7 | 7 | 7 | 7 | 3 |
| EXPLORATION | V2 | 17 | 17 | 17 | 17 | 11 | 11 | 11 | 11 | 5 |
| EXPLORATION | V3 | 318 | 294 | 271 | 271 | 160 | 131 | 127 | 126 | 74 |
| PINNACLE_ONLY_BENCHMARK | V1 | 20 | **0** | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Historical first losses (§2b.3) include:

- **Derek `SETTLEMENT_NOT_SUPPORTED`**: 164 fixtures, SOFTWARE.
- **`NO_RESEARCH_MODEL_CANDIDATE_EXISTS`**: 43 fixtures, SOFTWARE.
- **`STRATEGY_ENTRIES_DISABLED`** (10 fixtures) and the lifecycle
  SHADOW_ONLY / QUARANTINED states (12 and 25 fixtures). These are ECONOMIC
  risk rails.
- **The strict Pinnacle-only benchmark**: 0 of 20 fixtures mapped
  (`SETTLEMENT_NOT_SUPPORTED`, SOFTWARE).

---

## 5. Why no forward candidate was positive: measured costs vs modelled costs

The terms below come from each forward bind evaluation's stored pre-outcome
attribution (step 3 §3.2, first evaluation per contract-side). They are per
contract, in fictional USD.

| Term | Basis | COMPLETED_GAME (194 cs / 137 fx / 143,603 contracts) | DEREK (3 cs / 3 fx / 5,288 contracts) |
|---|---|---|---|
| A. Probability edge over the held-side mid | pre-outcome | **+0.04438** | **+0.13248** |
| B. Spread (mid → best) | **MEASURED** (book) | −0.00874 | −0.00261 |
| C. Slippage (best → walked VWAP) | **MEASURED** (book) | −0.00242 | 0.00000 |
| D. Fees | **MEASURED** (simulator fee schedule) | −0.01487 | −0.00574 |
| **= after measured costs** | | **+0.01835 (+$2,635.88; positive on 194 of 194 cs)** | **+0.12413 (+$656.45)** |
| E. Adverse selection | MODELLED: max(IOC bound, learned markout); the learned markout is 0, so the IOC bound is what is charged | −0.00481 | −0.07962 |
| F. Residual haircut | MODELLED from realized residuals | **−0.13625** | 0.00000 |
| G. Freshness decay | MODELLED | −0.00025 | −0.00684 |
| H. Management / exit cost | MODELLED (learned exit rate × spread) | **−0.05180** | −0.06179 |
| **= uncalibrated, after modelled costs** | | **−0.17476 (positive on 1 of 194 cs)** | −0.02412 |
| I. Calibration adjustment | MODELLED (shrinks p toward the market) | −0.03462 | **−0.12229** |
| **= bind all-in EV given fill** | | **−0.20938 (0 of 194)** | **−0.14641 (0 of 3)** |

These are the **pre-outcome** inputs retained per candidate (§3.3, first
evaluation per contract-side):

| Input | COMPLETED_GAME | DEREK |
|---|---|---|
| Probability (mean p_raw → p_used) | 0.5927 → 0.5636 | 0.5277 → 0.4526 |
| Best executable price (mean) | 0.5633 | 0.4467 |
| Calibration weight, mean / max | 0.0257 / 0.3151 | 0.0117 |
| Calibration cell status | MEASURED 22, INSUFFICIENT 118, NO_DATA 71 (211 cs) | INSUFFICIENT 3 |
| Partial-fill assumption: modelled fill probability | 0.5226 | 0.7907 |
| Policy size (mean) | 745.9 contracts | 1,762.7 contracts |
| Expected hold (capital time) | 10.89 h | 1.38 h |
| Input age | 0.80 s | 8.15 s |
| Spread | 0.0284 | 0.0350 |
| EV per capital-hour | not computed: the bind refuses before the capital-hour step | same |

**How to read the table.**

- **Pinnacle-only benchmark.** After the costs that are measured, it still
  shows +1.8¢ per contract, priced on Pinnacle's probability. The modelled
  costs remove it. Most of that is the residual haircut, which the bind
  learned from this strategy's own realized shortfall against expectation.
- **Derek.** The calibration rule is the binding term. It pulls the price
  toward the market by 0.122, because the blended probability has almost no
  settled calibration evidence (weight 0.0117). With weights this small,
  p_used is approximately the market price, so all-in EV is non-positive
  **by construction** until calibration evidence exists. That part is
  **missing evidence**, not a software defect.
- **Calibration and probability quality** (RC4 packet, not recomputed here):
  - `profitability_scoreboard.json` calibration: n = 164, Brier 0.167806
    against the market price's 0.167573, ECE 0.062. The used probability is
    no better than the market price.
  - `completion.json` probability: logloss minus market −0.01358 (90% CI
    [−0.0249, −0.0023]) over 556 decisions / 255 events. The packet itself
    labels this "not a frozen forward holdout", with authority
    `MARKET_PRIOR_ONLY` and reason `RESIDUAL_ALPHA_NOT_PROVEN`.

---

## 6. Settled net outcomes: the realized PAPER record, reconciled to the ledger

### 6.1 Reconciliation (step 4 §4.1–4.2): exact

| | USD |
|---|---|
| Realized P&L, sum over all 517 positions (514 closed, 3 open) | **−44,374.0962** |
| Ledger cash (seq 2305, last committed 2026-10-08 08:48:34Z) | 454,258.8638 |
| − starting cash + open cost basis (1,367.04) | −44,374.0962 |
| **Identity residual** | **0.000000** |

Each ledger kind matches its records exactly:

- FILL (232,634.9544) equals buys gross + fees.
- SALE (156,178.0082) equals sells gross − fees.
- SETTLEMENT (30,715.81) equals the latest settlement payouts.
- CORRECTION is 0.

This also matches Audrey's 11:00Z hourly: realized −$44,374.10, training
−$27,723.98, investment/benchmark −$16,650.12, 3 open, ledger seq 2305. The
investment/benchmark sleeve is split below, so the Pinnacle-only benchmark is
not presented as proprietary.

### 6.2 Per evidence class, strategy and frozen policy (step 4 §4.3–4.4, §4.6–4.7)

| Class | Strategy / policy | Positions (closed) | Fixtures | Realized | Expected at entry (policy, pre-outcome) | Residual | Fees (measured) | Capital-hours | $ per capital-hour | Max drawdown |
|---|---|---|---|---|---|---|---|---|---|---|
| **PROPRIETARY_BETTOR** | DEREK V2 | 145 (144) | 52 (51 closed) | **−13,860.58** | +4,124.40 | −17,984.98 | 2,564.34 | 728,724.9 | −0.0190 | 13,996.95 |
| **BENCHMARK_PINNACLE_ONLY** | COMPLETED_GAME (all) | 63 (63) | 37 | **−2,789.54** | +1,244.31 | −4,033.85 | 1,013.45 | 142,861.2 | −0.0195 | 3,083.74 |
| | COMPLETED_GAME V2 | 3 | 2 | +193.16 | +92.96 | +100.20 | 96.96 | 8,571.0 | +0.0225 | none |
| | COMPLETED_GAME V3 | 60 | 35 | −2,982.70 | +1,151.35 | −4,134.05 | 916.49 | 134,290.3 | −0.0222 | none |
| **TRAINING** | EXPLORATION (all) | 309 (307) | 142 (141 closed) | **−27,723.98** | −8,093.41 | −19,630.57 | 6,363.73 | 2,782,291.5 | −0.0100 | 27,779.03 |
| | EXPLORATION V1 / V2 / V3 | 7 / 11 / 291 | 7 / 11 / 126 | −289.10 / −1,392.65 / −26,042.23 | −26.66 / −316.46 / −7,750.29 | −262.44 / −1,076.19 / −18,291.94 | 31.93 / 239.05 / 6,092.75 | none | none | none |
| **ALL** | | 517 (514) | 141 | **−44,374.10** | −2,724.70 | −41,649.40 | 9,941.52 | 3,653,877.7 | −0.0121 | 44,622.32 at daily-close granularity, from §4.7 |

- **Outcomes.** 194 positions settled (54 WON, 140 LOST, 0 VOID) and 320
  closed by sale.
- **Window.** Entries ran from 2026-10-01 20:42Z to 2026-10-06 00:47Z.
- **Daily realized** (§4.7):
  - Derek: 10-05 −6,957.55, 10-06 −5,068.83, 10-07 −1,834.20.
  - Benchmark: 10-02 +193.16, 10-03 −242.77, 10-04 +17.88, 10-05 −174.78,
    10-06 −2,605.94, 10-07 +22.90.
  - Training: losses on six of seven days.

### 6.3 Measured execution (step 4 §4.8): partial fills, churn

| Strategy | ENTRY orders | Filled / partial / unfilled | Quantity fill rate (MEASURED) | Re-entries on an already-filled contract-side (churn) | Style |
|---|---|---|---|---|---|
| DEREK | 163 | 145 / 24 / 18 | 0.8418 | **75** (145 filled entries on 70 contract-sides) | MARKETABLE / IOC |
| COMPLETED_GAME | 109 | 63 / 8 / 46 | 0.3431 | 14 | MARKETABLE / IOC |
| EXPLORATION | 409 | 309 / 17 / 100 | 0.7396 | 53 | MARKETABLE / IOC |

Spread, slippage and adverse selection were **not** recorded separately for
the historical fills. The attribution only exists from the bind onward, so
they sit inside the realized cost. This is the RC4 capital-readiness producer
gap ARCHER `NO_RECORDED_EXPECTED_FILL_COST_PER_ORDER`. Fees are measured:
$9,941.52 on $232,634.95 bought.

**ECONOMIC_DUPLICATE_SUSPECT fills** (§4.9): 18 groups, 53 extra fills,
3,732.79 extra contracts, all SELL fills of STANDING_PROTECTION orders. Their
effect on realized P&L is **+$64.80** (Derek +1.46, benchmark +4.01, training
+59.34). The history is append-only and not rewritten, and the amount is
immaterial to every verdict below.

### 6.4 Uncertainty, clustered by fixture

`research/ef_cluster_bounds.py` reads the 229 cluster values from run
37790252179. The bound is one-sided 98%: Student-t with df = n − 1, and a
seeded cluster bootstrap (20,000 resamples).

| Unit | Clusters n | Mean net per fixture | sd | 98% LB (t) | 98% UB (t) | 98% LB (bootstrap) | Skew | Worst fixture | Mean without the worst |
|---|---|---|---|---|---|---|---|---|---|
| PROPRIETARY: DEREK, realized | 51 | **−271.78** | 1,296.96 | −654.74 | +111.19 | −691.94 | −3.82 | −7,876.90 | −119.67 |
| DEREK, realized − expected | 51 | −352.65 | 1,354.92 | −752.73 | +47.43 | −794.65 | −4.05 | none | none |
| BENCHMARK: COMPLETED_GAME, realized | 37 | **−75.39** | 243.66 | −160.75 | +9.96 | −165.99 | −2.55 | −985.61 | −50.11 |
| COMPLETED_GAME, realized − expected | 37 | −109.02 | 282.39 | −207.95 | **−10.10** | −213.30 | none | none | none |
| TRAINING: EXPLORATION, realized | 141 | **−196.62** | 473.23 | −279.24 | **−114.01** | −281.83 | −1.25 | none | none |
| PORTFOLIO, all strategies, clustered across strategies | 141 | **−314.71** | 1,033.15 | −495.08 | **−134.34** | −505.66 | −3.76 | −8,249.60 | −258.03 |
| PORTFOLIO, proprietary + benchmark | 67 | −248.51 | 1,154.93 | −544.13 | +47.11 | −579.35 | −4.19 | none | none |

**Is the bound statistically justified?**

- **Forward cohort: no.** It has n = 0 settled forward trades.
- **Historical cohort: computed, with caveats stated.**
  - The clusters span only 2 to 7 calendar days (2026-10-01 to 10-08) and
    share days and sports, so they are not fully independent.
  - The distributions are heavily left-skewed. One Derek fixture,
    −$7,876.90, is 57% of Derek's loss.
  - The policies changed during the window (V1 → V3).

  These caveats can only widen the bounds. Every mean is negative, so **no
  caveat can make a lower bound positive.**
- **Two results are significantly negative:** training (UB −114.01) and the
  whole portfolio (UB −134.34).
- **The Pinnacle-only benchmark's residual is significantly negative**
  (UB −10.10): realized results were worse than the policy expected at entry.

---

## 7. Hypothetical counterfactual results (`NOT_REALIZED_PNL`, never summed with PAPER)

| Source | Rows (all) | Settled | Basis | Clustered: fixtures / contract-sides | Clustered P&L | Mean per fixture (98% LB) |
|---|---|---|---|---|---|---|
| 311 variants, forward, COMPLETED_GAME **POLICY_SIZE = HOLD_TO_SETTLEMENT** | 1,518 | 1,104 | bind refused | 92 / 149 | **−765.15** (bind-expected −9,979.89 / −13,081.40) | −8.32 (t: −70.17, UB +53.54) |
| 311 variants, COMPLETED_GAME HALF_SIZE | 1,518 | 1,104 | none | 92 / 149 | −361.01 | −3.92 (normal: −34.36) |
| 311 variants, COMPLETED_GAME **MAKER** | 1,518 | 1,104 | none | 92 / 149 | −9.17 (all rows +1,062.10) | −0.10 (normal: −23.19) |
| 311 variants, DEREK (policy / hold) | 7 | 1 | none | 1 / 1 | −398.20 | n/a |
| 305 shadows, COMPLETED_GAME, `CASH_WAIT_FORWARD_ECONOMICS_NEGATIVE` (06:08–17:36Z on 10-06) | 546 | 473 | none | 34 / 45 | −5,179.81 (all rows −33,861.84) | −152.35 (normal: −344.09) |
| 305 shadows, EXPLORATION, lifecycle quarantined | 336 | 261 | none | 28 / 38 | −789.53 (all rows −32,069.01) | −28.20 (normal: −150.78) |

The all-rows scoreboard figures re-count the same contract many times. The
hold variant's all-rows total is 4.8 times its clustered figure, and the
shadow totals are 6.5 and 40 times theirs. The RC4 scoreboard's
"HOLD_TO_SETTLEMENT|CASH −4,820.31 over 1,086 settled" is on that basis. **Use
only the clustered columns.**

### Forward refused Pinnacle-only candidates: expectation vs outcome

These are 92 fixtures (`research/data/ef_forward_refused_run37790252179.csv`):

| Per fixture | Mean | 98% t interval |
|---|---|---|
| EV after measured costs (pre-outcome) | +26.15 | [+1.40, +50.90] |
| Settled counterfactual P&L (hypothetical) | −8.32 | [−70.17, +53.54] |
| Outcome − EV after measured costs | −34.47 | [−106.71, +37.77] |
| Outcome − bind all-in EV | **+263.74** | [**+167.86**, +359.63] |

**Reading.**

- The Pinnacle-only edge after measured costs, +$0.0208 per contract
  pre-outcome, **did not materialize**. The outcome was −$0.0066 per
  contract. The difference is not statistically resolvable at 92 fixtures.
- The bind's modelled costs were **significantly more pessimistic** than the
  outcomes of this forward sample. The residual haircut over-corrects here.
  It is still directionally right: realized came in below the measured-cost
  expectation.
- Neither expectation shows a positive edge.

---

## 8. Non-directional mechanisms (step 5 runs 37790435499, 37791068220)

**XAVIER management.** Theses: 471 (Derek 145, benchmark 52, training 274).
Value-add FINAL: 200, not final: 16, **no value-add row: 255**. The 200 FINAL
theses cover 86 fixtures:

| | Value |
|---|---|
| ACTUAL_XAVIER realized | −20,573.69 |
| HOLD_TO_SETTLEMENT counterfactual | −22,670.73 |
| IMMEDIATE_EXIT counterfactual | −8,579.88 (8 missing) |
| Actual − hold | +2,097.05 (mean +24.38 per fixture, normal 98% LB −123.62) |
| Actual − exit | −10,643.20 (mean −128.23, LB −343.83) |

By strategy, actual − hold / actual − exit:

- Derek: −202.75 / −7,512.60.
- Benchmark: +851.00 / +255.11.
- Training: +1,448.80 / −3,385.71.

**Not established.**

**ADRIANA (SHADOW ONLY).** 849 scans (828 OK and 21 NO_EVIDENCE) from
2026-10-05 22:26Z to 2026-10-08 14:10Z:

- Markets read: 31,412, of which books fresh 11,682. Both are summed per scan.
- Structures evaluated: 5,570 (COMPLEMENT 5,038, MIDDLE_FLOOR 532), every one
  REFUSED. **Opportunities: 0.** Pair receipts: 0.
- Refusal codes:

  | Code | Count |
  |---|---|
  | `CLAIM_LEG_HAS_NO_EVALUABLE_ALIAS` | 2,506 |
  | `PAYOFF_FLOOR_BELOW_COST` | 1,524 |
  | `STALE_BOOK` | 1,166 |
  | `VOID_TERMS_NOT_ESTABLISHED` | 532 |
  | `BOOK_MISSING` | 256 |
  | `BOOK_TIME_IN_FUTURE` | 83 |
  | `NO_EXECUTABLE_DEPTH` | 35 |

- Skip codes: `MONEYLINE_TIE_AND_OVERTIME_TERMS_NOT_MODELLED` 34,094,
  `SLUG_GRAMMAR_NOT_READ` 3,183, `BOOK_READ_FAILED` 2,014.
- The newest scan reports Kalshi books `UNAVAILABLE`
  (`NO_KALSHI_BOOK_SOURCE`).
- None of these codes are in `refusal_taxonomy_table`, so they are
  UNCLASSIFIED, by name.

**ROUTING (counterfactual).** 8,828 receipts over 24 events: 6,709 chosen,
all with best venue KALSHI, and 2,119 `NO_ELIGIBLE_ROUTE`. Every receipt has
exactly **one** candidate, so there is **no runner-up and 0 comparable
pairs**. Lost candidates:

- Kalshi: `STALE_BOOK` 808, `BOOK_TIME_IN_FUTURE` 630, `INSUFFICIENT_DEPTH` 365.
- PMUS: `NO_BOOK` 218, `STALE_BOOK` 96, `BOOK_TIME_IN_FUTURE` 2.

These codes are UNCLASSIFIED by the taxonomy. Savings are unmeasurable.

**ALLIE.** 87,486 SHADOW rows over about 656 runs. The shadow_usd totals are
NEW_DECISION $2.10 and OPEN_POSITION $290.79. Allie has no allocation
authority, so no realized dollar exists.

---

## 9. Capacity, drawdown, champion / CASH

- **Capacity.**
  - Proven positive capacity is **$0**:
    - RC4 `capital_readiness` capacity is RED (`NO_POSITIVE_CAPACITY`,
      max_positive_qty 0).
    - Every strategy's revenue readiness `executable_capacity_usd` is 0.
    - In the forward bind, every candidate is refused at all-in EV ≤ 0,
      **before** the depth- and correlation-constrained capacity frontier is
      reached, so the marginal-EV frontier admits 0 contracts.
  - Displayed depth is not the binding constraint: the benchmark policy wanted
    143,603 contracts over 194 forward contract-sides, but the economics
    refused all of them.
- **Drawdown.** Derek 13,996.95, benchmark 3,083.74, training 27,779.03. The
  portfolio is 44,622.32 at daily-close granularity. Equity drawdown since
  funding is 45,741.14 by cash alone.
- **Champion and lifecycle** (step 0 §0.11):

  | Strategy | Transition | Trigger |
  |---|---|---|
  | DEREK | QUARANTINED 2026-10-06 06:27:02Z | LOSS_BUDGET_QUARANTINE at −10,784.36 (budget −10,000) |
  | EXPLORATION | QUARANTINED 05:37:53Z | LOSS_BUDGET_QUARANTINE at −10,272.81 |
  | COMPLETED_GAME | REDUCED_SIZE 22:26:09Z | LOSS_BUDGET_REDUCE at −2,812.44 |
  | COMPLETED_GAME | QUARANTINED 23:30:11Z | PROFITABILITY_RESIDUAL_DETERIORATION_QUARANTINE: n 239, all shadow, residual −7,334.72 |

  The strategy tournament's incumbent and selection are both **CASH**
  (`NO_CANDIDATE_BEATS_CASH_ON_ABSOLUTE_FORWARD_ECONOMICS`). The capital
  readiness observer is RED with recommended capital $0, and per the
  integrator the scale observer was RED
  `NO_ENTERED_EVALUATION_WITH_COMPLETE_TERMS_IN_WINDOW` at 02:41Z.

---

## 10. The 98% verdict: NOT ESTABLISHED, and exactly what is missing

| Mechanism | Forward (frozen bind) | Historical | Failed economics (measured) | Missing observations or producers |
|---|---|---|---|---|
| **PROPRIETARY: DEREK V2** | 0 settled forward fixtures | 51 fixtures, mean −271.78, LB −654.74 | Realized −13,860.58 against expected +4,124.40. The internal model sits +10.8 pp above Pinnacle on admitted candidates and did not realize. | (1) ≥ 100 independent settled forward fixtures under one frozen policy, which need (2) a named person to lift QUARANTINE and (3) settled calibration observations for the blended probability (weight 0.0117 today). (4) SOFTWARE: settlement-terms resolution for 76 of 79 policy-positive forward fixtures. |
| **BENCHMARK: COMPLETED_GAME V3 (not proprietary)** | 0 | 37 fixtures, mean −75.39, LB −160.75; residual significantly negative | Bind all-in EV ≤ 0 on 100% of forward candidates. Outcomes of the refused candidates came to −765.15 over 92 fixtures. | Even if the measured-cost expectation (+$26.15 per fixture) were the true mean, a positive one-sided 98% bound at the observed forward sd ($284.73) needs **≈500 independent settled fixtures**. Also blocked by QUARANTINE. |
| **TRAINING: EXPLORATION V3** | 0 | 141 fixtures, mean −196.62, 98% interval [−279.24, −114.01] | Significantly negative. This is a research cost by design and never a profit claim. | Bind verdict on forward entries not persisted (§12, F1). |
| XAVIER overlay | none | 86 fixtures, +24.38 per fixture against hold (LB −123.62); −128.23 against exit | Negative against immediate exit | Value-add rows for 255 of 471 theses. |
| ADRIANA | 0 opportunities | none | 1,524 `PAYOFF_FLOOR_BELOW_COST` | A Kalshi book source in Postgres; evaluable aliases and void terms; tie and overtime terms (34,094 skips). |
| ROUTING | 0 comparable | none | none | A second candidate route per claim (PMUS books `NO_BOOK` / `STALE_BOOK`). |
| ALLIE | none | none | none | No authority, by design. |
| **PORTFOLIO** | 0 | 141 fixtures, mean −314.71, 98% interval [−495.08, −134.34] | **Significantly negative** | none |

**No candidate is claimed profitable.** The PM's per-candidate fields are
given anyway for the candidates that exist:

- Frozen pre-outcome probability, EV and size: §5.
- Calibration: §5.
- Measured vs modelled costs, including fees, spread, slippage, adverse
  selection, churn, partial fills and capital time: §5, §6.3 and §6.2.
- Realized net P&L reconciled to the ledger: §6.1.
- Expected-vs-realized residuals: §6.2 and §6.4.
- Independent event counts and the testing method: §6.4.
- Drawdown and capacity: §9.

---

## 11. Consistent profitability for the 14-category scorecard: UNPROVEN

Profitability is classified on its own and is **not** an engineering score.
Moving it from UNPROVEN takes **all** of the following, measured in
production, for one mechanism under one frozen policy version entered after
the cutover:

1. **≥ 100 independent settled forward fixtures.** This is the scoreboard's
   `MIN_FORWARD_SAMPLE` and the PM pack's `directional.min_independent_events`.
   Each fixture counts once, whatever number of fills or contracts it holds.
2. **A one-sided 98% lower bound on mean net PAPER P&L per fixture above 0.**
   The bound is clustered and uses Student-t with df = n − 1, with a bootstrap
   beside it. The P&L is reconciled to `paper_ledger` within $0.01 and comes
   from an untouched holdout.
3. **The sample size the observed moments require**, (t·sd / mean)². This is
   about 500 fixtures at the Pinnacle-only measured-cost expectation and the
   observed dispersion.
4. **A positive lower bound on executable EV**, plus calibration measured in
   the traded cells (n_cell ≥ 30).
5. **Expected-vs-realized residuals not significantly negative.** Today the
   benchmark's residual 98% UB is −10.10.

**What blocks the generation of any forward PAPER evidence today:**

- All three strategies are QUARANTINED. Leaving QUARANTINED needs a named
  person (`bettor_strategy_lifecycle.transition`); nothing promotes
  automatically.
- The bind refuses every candidate. For Derek, settlement terms are
  unresolved (SOFTWARE).

Without an owner decision on lifecycle state, or a new frozen policy version,
**only counterfactual evidence accumulates**. Counterfactual evidence can't
move this category: the scoreboard counts settled forward PAPER trades only.
This report changes no gate, threshold or lifecycle state.

---

## 12. SOFTWARE and producer findings (named; not fixed here)

- **F1. The bind verdict on lifecycle-refused ledger entries is never
  persisted.**
  - Where: `bettor_capital_authority.after_ledger_refusal` (RC4) runs
    `PBIND.entry_bind` for a lifecycle-refused ENTRY. It keeps only
    `capital_eligible=False`.
  - What is lost: it writes no `paper_profitability_evaluations` row, and the
    census row's `refusals` holds only the lifecycle code.
  - Evidence:
    - 0 LEDGER-stage evaluations since the cutover (step 0 §0.4).
    - 1,510 exploration LEDGER refusals, all with recorded total executable
      EV > 0 (step 1 §1.7).
    - 0 shadow counterfactuals after 2026-10-06 17:36:45Z (step 0 §0.13).
  - Effect: for 153 training fixtures, S5c can only be inferred, not
    measured.
- **F2. `NO_DEPTH_AT_THE_BEST_LEVEL` is unclassified.** It is a literal in
  `agents/paper_explore.py:356` and is missing from `refusal_taxonomy_table`.
  Forward: 178 decisions, 32 fixtures.
- **F3. Adriana and routing codes are unclassified.** None are in the refusal
  taxonomy: `CLAIM_LEG_HAS_NO_EVALUABLE_ALIAS`, `PAYOFF_FLOOR_BELOW_COST`,
  `STALE_BOOK`, `VOID_TERMS_NOT_ESTABLISHED`, `BOOK_MISSING`,
  `BOOK_TIME_IN_FUTURE`, `NO_EXECUTABLE_DEPTH`, `NO_ELIGIBLE_ROUTE`,
  `INSUFFICIENT_DEPTH` and `NO_BOOK`. The 34,094
  `MONEYLINE_TIE_AND_OVERTIME_TERMS_NOT_MODELLED` skips are a capability gap.
- **F4. Derek's settlement terms are unresolved.** 76 of 79 forward
  policy-positive fixtures are `CAPITAL_INELIGIBLE_SETTLEMENT_TERMS_UNRESOLVED`
  (SOFTWARE / SETTLEMENT). The benchmark policy passes capital eligibility on
  its own contract match.
- **F5. The revenue-readiness scoreboard does not reconcile to the ledger.**
  Its agent figures cover only FINAL `xavier_value_add` theses: 200 of 517
  positions, realized −20,573.69 against the ledger's −44,374.10. It counts
  25 / 8 / 51 events where the full record has 51 / 37 / 141 fixtures. The
  ledger total itself reconciles exactly (§6.1). 255 theses have no value-add
  row.
- **F6. Duplicate fills.** The 18 ECONOMIC_DUPLICATE_SUSPECT groups change
  realized P&L by +$64.80. They are historical and not rewritten.

The report also corrects a reading of its own query. In step 4 §4.4 the
strategy subtotal rows count (policy, fixture) pairs: training shows 144
there. The bounds in §6.4 use the unique fixtures from step 6 (141), and the
per-policy rows in §4.4 are correct.

---

## 13. What is proven here, and what still needs production evidence

**Proven, from production rows in the runs above:**

- The funnel counts at each stage, per strategy and policy, for both cohorts.
- The exact ledger reconciliation.
- The realized P&L per evidence class, strategy and policy, and the
  clustered moments.
- The measured and modelled cost terms the bind recorded.
- The lifecycle states.
- The non-directional mechanisms' counts.

**Computed offline from those values:** the Student-t and bootstrap bounds,
and the required-sample figure. Both scripts and the input CSVs are committed.

**Not measured, and named as such:**

- The bind verdict on forward exploration entries (F1).
- Historical spread, slippage and adverse selection per fill. These predate
  the attribution.
- Xavier value-add for 255 theses.
- Hypothetical outcomes for Derek's 76 settlement-ineligible forward fixtures.
  No producer records a counterfactual for a candidate refused before the
  bind.
- Calibration recomputed here: it is cited from the RC4 packet, because the
  outcome orientation (`paper_xavier.outcome_for`) is Python-only.
- Unrealized P&L on the 3 open positions: −$95.84 per Audrey at 11:00Z, not
  recomputed here.

**Time-dependence.** The forward counts are as of 14:02–14:12Z on 2026-10-08
and grow with every valuation. Re-running the files reproduces them as of the
new now().
