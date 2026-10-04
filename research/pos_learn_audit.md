# POS part 3: evaluation and learning (audit)

Branch `claude/pos-learn`, base `4717460d7d19acd43eb5e48593abe2cf658f3a62` (production).
SHA: the commit that adds this file. A commit cannot name its own hash, so read it with `git log -1 -- research/pos_learn_audit.md`.
Migration `218_model_agent_tournaments.sql`, rolled back by `rollback/218_model_agent_tournaments.down.sql`.

**Authority: SHADOW / RESEARCH, none.** Nothing here changes a production probability, threshold, size, allowlist, order or capital. Nothing in production reads a `poslearn_*` table, and `test_poslearn_authority` pins that. Promotion follows one path: predeclared criteria met → Karen challenge → Audrey evaluation → a **human** approval record that no agent and no code path writes. Even that record has `production_effect = 'NONE'`; a promoted model still needs a separate owner release.

Nothing is COMPLETE. Gate, deployment and production readback are **PENDING** for every row. No row has a forward sample in production yet, because nothing has been deployed.

---

## 1. Audit matrix (one row per capability)

Test abbreviations (all in `backend/tests/`):
- **T-AUTH**: `test_poslearn_authority.py`
- **T-TOUR**: `test_poslearn_tournaments.py`
- **T-MM**: `test_poslearn_models_and_meta.py`
- **T-DEF**: `test_poslearn_overfitting_defences.py`

| CAPABILITY | OWNER | BRANCH | SHA | MIGRATION | DATA CONTRACT | AUTHORITY LEVEL | UNIT | INTEGRATION | CAPITAL-BOUNDARY | OOS | COUNTERFACTUAL | FAIL-CLOSED TEST | AUDREY AUDIT | KAREN REVIEW | COMMAND CENTER | SLACK | EXACT-SHA GATE | DEPLOYMENT | PRODUCTION READBACK | FORWARD SAMPLE | ECONOMIC RESULT | STATUS | BLOCKER |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 Model tournament: M0_PINNAPI_RAW champion; M1 calibrated, M2 microstructure, M5 sport-specific (NFL/MLB/NCAAF/NBA/NHL/SOCCER/TENNIS) | Derek (entry model) · runner | claude/pos-learn | this commit | 218 | `poslearn_registrations` (immutable, hashed criteria) · `poslearn_opportunities` · `poslearn_forecasts` · `poslearn_outcomes` | SHADOW_RESEARCH_NO_AUTHORITY | T-MM (train, forecast, fixed-sample verdict, Bonferroni, coverage) | T-TOUR §1 (3 cycles end to end; same events; verdict CRITERIA_MET → Karen → Audrey → human gate) | T-AUTH §1–§4, T-TOUR §1 (protected tables unchanged per cycle) | Forward only: forecasts are refused for opportunities before registration or after an outcome (T-TOUR §3). Training uses only outcomes known at registration (T-DEF D2) | Each challenger is paired with the champion on the same opportunities; decision rule = approved threshold | T-TOUR §3 (late/early forecast refused); a training fit that fails to converge is refused (T-MM) | Independent recompute on the fixed sample; disagreement or failed criteria → FAIL (T-TOUR §1) | Promotion challenge: time halves, sport, outliers, regime, forward-only (T-MM, T-DEF D8/D9/D11) | GET /api/command/tournament/models | n/a | PENDING | PENDING | PENDING | 0 (not deployed; synthetic only in tests) | UNPROVEN | SHADOW | Deployment and forward sample |
| 1a M3_PINNAPI_PLUS_SCOUT | Derek · Scout (future) | claude/pos-learn | this commit | 218 | registration placeholder, `status = AWAITING_FEATURES` | none (forecasts refused by DB) | T-MM (not fitted, reason stated) | T-TOUR §1 (registered AWAITING_FEATURES) | T-TOUR §3 (a placeholder cannot forecast) | n/a | n/a | DB refuses forecasts unless ACTIVE_FORWARD | n/a | n/a | /tournament/models (`placeholder.why`) | n/a | PENDING | PENDING | PENDING | 0 | UNPROVEN | BLOCKED | Scout features do not exist at this SHA |
| 1b M4_CROSS_MARKET_CONSENSUS | Derek | claude/pos-learn | this commit | 218 | registration placeholder, `status = AWAITING_SOURCE` | none | T-MM (reason names `valuation_corroboration`) | T-TOUR §1 | T-TOUR §3 | n/a | n/a | as 1a | n/a | n/a | /tournament/models | n/a | PENDING | PENDING | PENDING | 0 | UNPROVEN | BLOCKED | No independent second market price is recorded (see §6) |
| 2 Edge-confidence meta-model | Derek · Allocator (future consumer) | claude/pos-learn | this commit | 218 | META_MODEL registration; forecast columns `edge_confidence`, `expected_net_edge` (+95% CI), `output.q` with CI | SHADOW; `applied = false` CHECK; future sizing formula NOT_ACTIVE | T-MM (trained, frozen, monotone, null reasons; evaluation null until measured) | T-TOUR §1 (forward forecasts, forward evaluation n = 120) | T-AUTH; no reader outside the layer | Forward only; paired log loss of q vs raw p | Realized vs predicted net edge per EDGE_CONFIDENCE bucket | Missing cost → null with a reason (T-MM) | n/a (not promotable to sizing) | n/a | GET /api/command/profitability/edge-confidence | n/a | PENDING | PENDING | PENDING | 0 | UNPROVEN | SHADOW | Forward sample; never a live-sizing input |
| 3 Avoidance model | Derek · Karen (consumer of reasons) | claude/pos-learn | this commit | 218 | META_MODEL registration (segment table frozen in params); `avoidance_risk`, `avoidance_level` NORMAL/CAUTION/AVOID/UNMEASURED, `reasons` | SHADOW; `applied = false` | T-MM (learns failing segment, named reasons, price movement dimension; metrics null until measurable) | T-TOUR §1 (forward evaluation over champion entries) | T-AUTH | Forward only | Measured on the unfiltered champion entry set (T-DEF D10) | UNMEASURED when no segment has enough history | n/a | n/a | GET /api/command/profitability/avoidance | n/a | PENDING | PENDING | PENDING | 0 | UNPROVEN | SHADOW | Forward sample; blocks nothing |
| 4a Agent tournament: DEREK_V1 / CHALLENGER_A / _B | Derek | claude/pos-learn | this commit | 218 | AGENT_VARIANT registrations; one forecast row per opportunity | SHADOW | T-MM | T-TOUR §1 (n = 120, net economics measured) | T-AUTH | Forward only | Paired vs DEREK_V1 on the same opportunities; fixed sample | VOID counts as 0, not excluded (T-DEF D4) | Independent P&L recompute | Promotion challenge | GET /api/command/tournament/agents | n/a | PENDING | PENDING | PENDING | 0 | UNPROVEN | SHADOW | Forward sample |
| 4b XAVIER_V1 / CHALLENGER_A / _B | Xavier | claude/pos-learn | this commit | 218 | plan recorded before the outcome; replayed over `paper_book_observations` and later PinnAPI quotes | SHADOW | T-MM (path replay) | T-TOUR (real path replay: hold / take-profit / edge-loss exit) | T-AUTH | Forward only | Same entry set for every variant (T-DEF D7) | Unobserved path → hold, counted `path_unobserved` | Audrey FAILS CLOSED (path replay not independently recomputed) | Promotion challenge | /tournament/agents | n/a | PENDING | PENDING | PENDING | 0 | UNPROVEN | SHADOW | Audrey's independent path recompute is not built, so no Xavier variant can pass the ladder |
| 4c ALLOCATOR_V1 / CHALLENGER_A / _B | Chief Allocator | claude/pos-learn | this commit | 218 | `shadow_usd` per entry inside a $1,000 shadow sleeve per cycle | SHADOW; reuses intel/sizing.shadow_size | T-MM (sleeve never exceeded; EC-unavailable = $0, declared) | T-TOUR §1 | T-AUTH | Forward only | Same entry set (T-DEF D7) | EC unavailable → $0 with reason | Independent P&L recompute | Promotion challenge | /tournament/agents | n/a | PENDING | PENDING | PENDING | 0 | UNPROVEN | SHADOW | Forward sample |
| 4d EDDIE_V1 / CHALLENGER_A / _B | Eddie (claude/pos-agents) | claude/pos-learn | this commit | 218 | read-only interface `eddie_execution_estimates` (to_regclass); registered AWAITING_INTERFACE | none (forecasts refused) | T-MM | T-TOUR §1 (AWAITING_INTERFACE) | T-AUTH (`eddie` is never imported) | n/a | n/a | Unrecognised interface shape → absent, with reason | FAILS CLOSED | n/a | /tournament/agents | n/a | PENDING | PENDING | PENDING | 0 | UNPROVEN | BLOCKED | EDDIE not present on this base; interface schema not confirmed with claude/pos-agents |
| 5 Causal experiment platform | Audrey (randomization audit) · Karen (design challenge) | claude/pos-learn | this commit | 218 | `poslearn_experiments` (design fixed after start), `_assignments` (seeded, DB-verified, append-only), `_outcomes` (after assignment), `_reviews` | PAPER_SHADOW_ONLY | T-MM (draw = DB draw, power, ITT analysis, Karen blocks bad design, Audrey catches tampered arm) | T-TOUR §1 (registered → Karen → RUNNING → 240 assignments → outcomes → Audrey audits PASS) | T-AUTH | Assignment before outcome (DB) | Arms are shadow policies on the same opportunities (§6) | T-TOUR §5 (wrong arm/draw, reassignment, deletion, outcome-before-assignment, design change after start, start without Karen: all refused) | Recorded rows: AUDREY_RANDOMIZATION_AUDIT | Recorded rows: KAREN_DESIGN_CHALLENGE | GET /api/command/experiments | n/a | PENDING | PENDING | PENDING | 0 of 6,930 per experiment | UNPROVEN | SHADOW | Sample size: 3,465 per arm at the declared power |
| 6 Promotion ladder + human approval gate | Owner (human) | claude/pos-learn | this commit | 218 | `poslearn_promotion_steps` (ordered, actor per step, append-only); `poslearn_human_approvals` (write-once, approver never an agent) | none: records a human decision only | T-MM | T-TOUR §1/§4 | T-AUTH §2 (no code path writes the approval) | n/a | n/a | T-TOUR §4: skipped steps, wrong actors, champion as candidate, agent approvers, update/delete all refused | Step 3 | Step 2 | ladder + `promotion_stage` in both tournament reads | n/a | PENDING | PENDING | PENDING | 0 | n/a | SHADOW | none |
| 7 Runner | runner | claude/pos-learn | this commit | 218 | `poslearn_runs`, `poslearn_snapshots` | SHADOW; kill switch `POS_LEARN=off` | n/a | T-TOUR §1, T-AUTH §6 | T-AUTH (failure isolation, protected tables unchanged) | n/a | n/a | Failing component isolated; no migration → idles; advisory lock standby (T-AUTH §6) | n/a | n/a | n/a | n/a | PENDING | PENDING | PENDING | 0 cycles in production | n/a | SHADOW | Deployment |
| 8 Anti-overfitting defences D1–D11 | Audrey · Karen | claude/pos-learn | this commit | 218 | see §2 | n/a | T-DEF | T-DEF (pg tests) | n/a | D1/D2 | D10 | see §2 | D11 | D8/D9 | n/a | n/a | PENDING | PENDING | PENDING | n/a | n/a | SHADOW | none |

## 2. Anti-overfitting defences: mechanism and test

| # | Defence | Mechanism | Test(s) |
|---|---|---|---|
| D1 | Look-ahead | Features come only from rows at or before t: a book observation ≤ t and ≤ 300s old (`reads.books_asof`), quotes strictly before t, regime ≤ t, and a history index that refuses to move backwards and counts only outcomes known by t. The DB CHECKs `features_as_of ≤ opportunity_at ≤ captured_at` | T-DEF `test_d1_*` (3 tests), T-MM `test_build_names_what_is_missing_and_stores_no_outcome` |
| D2 | Train/test leakage | Training rows need `outcome_at ≤ registration`. The DB CHECKs `training_window_end ≤ registered_at` and refuses back-dated `registered_at` (±5 min of the DB clock). The forecast trigger refuses an opportunity that predates the registration, and the scorer ignores such rows anyway | T-DEF `test_d2_*`, T-TOUR `test_a_registration_is_immutable_hashed_and_not_backdated`, `test_a_forecast_is_before_its_outcome_and_after_registration` |
| D3 | Post-outcome features | `features.FORBIDDEN_KEYS` are never built. The DB CHECK refuses outcome-bearing keys in `poslearn_opportunities.features`. Forecast, opportunity and assignment triggers refuse rows once the outcome is known (here or on the source valuation) | T-DEF `test_d3_*`, T-TOUR §3 |
| D4 | Survivorship | VOID and UNVERIFIED outcomes are recorded and counted (VOID = 0 P&L, never dropped). Abstentions count against coverage. Fixtures resolved before capture are counted (`missed_outcome_known_before_capture`) | T-DEF `test_d4_*` (2 tests) |
| D5 | Multiple hypotheses | Family size is fixed by the declared plan, not by what trained: 11 model challengers, 8 agent challengers, 2 experiments, and every avoidance segment tested. Bonferroni-adjusted z is recorded in each registration: 2.838 for models, 2.734 for agents, 2.241 for experiments | T-DEF `test_d5_*`, T-MM `test_significance_at_005_is_not_enough_under_the_family_size` |
| D6 | p-hacking | A registration is immutable (trigger). Every verdict is a fixed-sample test on the FIRST `minimum_sample` pairs, decided once, with coverage frozen at the cut. A subject that failed forward is never re-registered (no retry until it passes). An experiment's design is immutable after start, it has one analysis, and no interim estimate is shown | T-DEF `test_d6_*` (3 tests), T-MM `test_the_fixed_sample_verdict_is_decided_on_the_first_n_only`, T-TOUR `test_an_experiments_design_is_fixed_once_started` |
| D7 | Selection bias | One shared opportunity set. Every ACTIVE registration gets a row for every opportunity it was offered (ABSTAIN is explicit, with a reason). Xavier, Allocator and Eddie act on the one entry set DEREK_V1's rule selects | T-TOUR §1 (per-opportunity row count equals active registrations), T-DEF `test_d7_*` |
| D8 | Regime overfitting | Karen blocks when the improvement fails in either time half or in the largest regime with ≥30 pairs | T-DEF `test_d8_*` |
| D9 | Sport leakage | M5 models train on their own sport only and abstain elsewhere. Karen blocks when dropping the top-contributing sport flips the sign | T-DEF `test_d9_*` |
| D10 | Counterfactual contamination | The randomization draw depends only on seed, experiment and unit. Meta-models are measured on the unfiltered champion entry set. No shadow output feeds a production decision (`applied = false`, no reader) | T-DEF `test_d10_*` (2 tests), T-AUTH §4 |
| D11 | Agent confirmation loops | The proposer (runner) is not the evaluator. Audrey's evaluation imports none of the runner's scoring code and reads raw rows itself. A Karen block ends the ladder. The champion cannot be a candidate. The human approver cannot be an agent or a ladder actor (DB) | T-DEF `test_d11_*`, T-AUTH `test_audrey_recomputes_without_the_runners_scoring_code`, T-TOUR §4 |

## 3. Checklist per capability (the brief's columns)

| Capability | DESIGNED | SCHEMA | CODED | UNIT TESTED | INTEGRATION TESTED | AUTHORITY TESTED | ECONOMIC METRIC DEFINED | SHADOW RUN | EVAL | COMMAND CENTER |
|---|---|---|---|---|---|---|---|---|---|---|
| Model tournament | yes | yes | yes | yes | yes (synthetic DB) | yes | yes (Brier, log loss, calibration, net predicted edge, realized edge, coverage, n, CIs) | synthetic test DB only; production PENDING | PENDING forward sample | endpoint yes; UI no |
| M3 / M4 placeholders | yes | yes | placeholder | yes | yes | yes | n/a | n/a | n/a | endpoint yes |
| Edge confidence | yes | yes | yes | yes | yes | yes | yes (realized vs predicted net edge by bucket; paired log loss vs raw p) | synthetic only | PENDING | endpoint yes |
| Avoidance | yes | yes | yes | yes | yes | yes | yes (losses avoided, false blocks, precision, recall, incremental economics) | synthetic only | PENDING | endpoint yes |
| Agent tournament (D/X/A) | yes | yes | yes | yes | yes | yes | yes (net economics, drawdown, capital-hours, turnover, fees, execution loss, false refusals, risk) | synthetic only | PENDING | endpoint yes |
| Agent tournament (EDDIE) | yes (interface) | yes | interface only | yes | yes (AWAITING_INTERFACE) | yes | defined; UNPROVEN | no | no | endpoint yes |
| Experiment platform | yes | yes | yes | yes | yes | yes | yes (realized net edge per unit, ITT) | synthetic only | PENDING | endpoint yes |
| Promotion ladder / human gate | yes | yes | yes | yes | yes | yes | n/a | synthetic only | n/a | in tournament reads |
| Runner | yes | yes | yes | n/a | yes | yes | n/a | synthetic only | n/a | n/a |

## 4. Schema (migration 218)

There are 12 tables, all `label = 'SHADOW'`:
- `poslearn_runs`
- `poslearn_snapshots`
- `poslearn_registrations`
- `poslearn_opportunities`
- `poslearn_outcomes`
- `poslearn_forecasts`
- `poslearn_promotion_steps`
- `poslearn_human_approvals`
- `poslearn_experiments`
- `poslearn_experiment_reviews`
- `poslearn_experiment_assignments`
- `poslearn_experiment_outcomes`

Guards (triggers / CHECKs):
- **registrations**:
  - The SHA-256 must equal the hash of the canonical text, and the document must equal that text.
  - The required criteria keys must be present.
  - The training window must close by registration.
  - No authority keys.
  - Content is immutable.
  - Status moves forward only (AWAITING_* → RETIRED; ACTIVE_FORWARD → RETIRED or FAILED_FORWARD).
  - Never deleted.
  - No back-dating.
  - One live version per subject.
- **opportunities**:
  - Append-only.
  - Refused when the source valuation already knows its outcome.
  - As-of CHECK.
  - No outcome-bearing feature keys.
  - One opportunity per fixture.
- **outcomes**: append-only.
- **forecasts**:
  - Append-only.
  - Registration must be ACTIVE_FORWARD.
  - The opportunity cannot predate the registration.
  - The outcome must not be known.
  - `applied = false`.
  - No authority keys in the output.
- **promotion_steps**:
  - Append-only.
  - One row per step.
  - The actor is fixed per step (POS_LEARN_RUNNER / KAREN / AUDREY).
  - Order is enforced.
  - CHALLENGER registrations only.
  - Nothing after CLOSED.
- **human_approvals**:
  - Write-once.
  - Requires Audrey PASS first.
  - The approver is never an agent, system or migration actor (`poslearn_is_agent_actor`), and never a ladder actor.
  - The statement must be 20 characters or more.
  - `production_effect = 'NONE'`.
- **experiments**:
  - Inserted REGISTERED.
  - The design is immutable once started (`status <> REGISTERED` or `now() ≥ start_at`).
  - RUNNING requires a KAREN_DESIGN_CHALLENGE review that did not block.
  - Status moves forward only.
  - The result is write-once and only on ANALYZED.
  - Never deleted.
  - Scope is PAPER_SHADOW_ONLY.
- **assignments**:
  - Experiment must be RUNNING.
  - Inside the start/stop window.
  - Draw and arm are recomputed with `poslearn_draw(seed, experiment, unit)` and must match.
  - Before the outcome.
  - No UPDATE or DELETE.
  - `recorded_at` comes from the DB clock.
- **experiment_outcomes**:
  - Append-only.
  - Must follow the persisted assignment, by DB clock.
- **reviews**:
  - Append-only.
  - kind/actor pairs are fixed: KAREN_DESIGN_CHALLENGE by KAREN, AUDREY_RANDOMIZATION_AUDIT by AUDREY.

## 5. Endpoints (GET only, `require_read`, 401 without a session)

Every response is the envelope `{label: SHADOW, authority: SHADOW_RESEARCH_NO_AUTHORITY, disclosure, status: OK|EMPTY|UNAVAILABLE, why, run_id, computed_at, data}`. EMPTY names the reason (`NO_LEARNING_RUN_YET`, `MIGRATION_218_NOT_APPLIED`).

- `/api/command/tournament/models`: `data.champion` and `data.models[]`. Each model carries:
  - `registration_id`, `sha256`, `status`, `role`
  - `offered`, `forecast`, `resolved`, `coverage`
  - `brier`, `log_loss` with CIs; `calibration{reliability, decomposition}`
  - `net_predicted_edge_mean`, `realized_edge_mean`, `realized_edge_ci`
  - `versus_champion{verdict, log_loss_improvement, ci_adjusted, brier_improvement, z_adjusted, family_size, sample_ids_sha256}`
  - `document_summary`, `promotion_ladder[]`, `promotion_stage`

  The payload also carries `data.not_registered{subject: reason}`, `data.feature_catalog[]` and `production_effect: NONE`.
- `/api/command/tournament/agents`: `data.variants[]`, each with:
  - `subject_id`, `agent`, `policy`, `status`
  - `metrics{net_economics_usd, max_drawdown_usd, capital_hours, turnover_usd, fees_usd, execution_loss_usd, false_refusals, largest_position_usd, sport_concentration, acted, path_unobserved}`
  - `versus_v1{verdict, mean_difference, ci_adjusted}`
  - `promotion_ladder`, `promotion_stage`
- `/api/command/profitability/edge-confidence`:
  - `data.status`: SHADOW_FORWARD or NOT_REGISTERED, with `why`
  - `registration`, `features_used`, `features_unavailable`, `feature_catalog`
  - `forward_evaluation{brier_q, log_loss_q, paired_log_loss_improvement_vs_raw, by_edge_confidence_bucket[]}`
  - `latest[]`, `future_sizing_formula{status: NOT_ACTIVE}`, `live_sizing_effect: NONE`
- `/api/command/profitability/avoidance`:
  - `data.status`, `segments_flagged`, `levels`
  - `forward_evaluation{AVOID, AVOID_OR_CAUTION: {blocked, losses_avoided_per_contract, good_trades_falsely_blocked, precision, recall, incremental_economics_*}}`
  - `latest[]`, `blocks_production: false`
- `/api/command/experiments`: `data.experiments[]`, each with:
  - the full predeclared design, `status`, `assigned`, `assigned_by_arm`, `outcomes`, `reviews[]`
  - `interim_estimate`: NOT_SHOWN until ANALYZED
  - `result`

## 6. Simplifications versus the spec (honest list)

1. **The human gate is enforced by a DB CHECK, not by identity.** The database cannot cryptographically tell a human from an agent that holds the same database credential. The gate is the 201/204 pattern, tightened:
   - an actor CHECK that rejects every agent, system and migration name;
   - Audrey PASS required first;
   - the approval is write-once;
   - no code path in the repository writes it (static test).
2. **Karen and Audrey are deterministic code paths, not the LLM agents.** `poslearn/karen_review.py` and `poslearn/audrey_review.py` write rows under their actor names, as `intel/audrey_intel_risk` does. Karen's promotion challenge does not use `karen_challenges`, because that table's `target_agent` CHECK admits only DEREK/XAVIER/AUDREY. Audrey's independence means separate code (it imports none of the runner's scoring) plus her own SQL.
3. **All production valuations are CALIBRATION_ONLY under P5.** They carry only a displayed price, so the tournament's economics use it, labelled `DISPLAYED_NOT_EXECUTABLE`. It is research only, never an order price.
4. **The opportunity is the first unresolved PinnAPI valuation per fixture (`event_key`) seen within a 6h lookback.** A fixture first seen after an earlier valuation fell out of the window is captured at its later valuation. Fixtures resolved before any cycle are counted, not captured.
5. **Decision rule.** Every model applies the same approved threshold to its own probability, so economic differences come only from the probabilities. Alternative thresholds are agent and experiment variants (DEREK_CHALLENGER_A).
6. **M4 is AWAITING_SOURCE.** Every probability in the repo is Pinnacle (pinnapi raw websocket, or the legacy the-odds-api Pinnacle book). `valuation_corroboration` (195) has no writer and no probability. No Kalshi price is recorded for the same events. PinnAPI-vs-Polymarket disagreement is used as a feature, not as a consensus model.
7. **M3 is AWAITING_FEATURES.** Scout does not exist at this SHA. It will be re-registered as a new version once features exist.
8. **M2 needs a venue book within 300s before t and abstains otherwise.** It is not registered while training rows carry no book; production history decides that.
9. **A defect in reused code: `intel/calibration.fit_overlay` diverges on extreme probabilities.** It is undamped Newton started from (a=0, b=1), and it returned a=5000, b=587461 on p in {0.03, 0.97}. M1 and M5 accept its fit only if it is finite, bounded and no worse than identity in likelihood; otherwise they use a damped IRLS and record why. intel is **not** modified here. The intel overlay register itself may hold such a fit: flag for the intel owner.
10. **Edge confidence.**
    - Uses a Laplace approximation, not a bootstrap.
    - Sport is not a feature (that limits overfitting; avoidance segments by sport instead).
    - A missing feature is set to its training mean.
    - A feature present in under 10% of training rows is dropped.
    - Unavailable features: settlement confidence (only a settlement family is recorded), Karen challenge state (not keyed by opportunity) and Scout state. Execution latency is proxied by quote age, and true order-to-fill latency needs EDDIE.
    - EDDIE execution uncertainty is interface-only.
    - The target collapses to the outcome for a $0/$1 contract (stated in the module). EDGE_CONFIDENCE is P(expected net edge > 0).
11. **Avoidance.**
    - Univariate segments only (no interactions), with Bonferroni across the segments tested.
    - A fourth level, UNMEASURED, beyond NORMAL/CAUTION/AVOID, because absence of evidence is not NORMAL.
    - AVOIDANCE_RISK is the maximum normal-approximation P(segment mean realized < 0).
12. **Agents.**
    - Xavier variants replay top-of-book (`paper_book_observations`) and later PinnAPI quotes, with no depth walk. An unobserved path counts as a hold.
    - Execution loss is half-spread against the venue mid at t, not real slippage.
    - Allocator V1 reuses `intel/sizing.shadow_size` with its stated unmeasured defaults.
    - EDDIE assumes the interface `eddie_execution_estimates(us_market_slug, execution_uncertainty, fill_probability, estimated_at)`. Any other shape reads as absent, with a reason. This needs confirming with claude/pos-agents.
    - Audrey fails closed on Xavier and Eddie candidates, because the path and fill recompute is not built. No Xavier or Eddie variant can pass the ladder yet.
13. **Experiments are randomized evaluations of shadow policies on the same opportunities.**
    - Only the 2 threshold experiments are declared. Management, execution, sizing, feature and allocation experiments are supported by the platform (`experiments.PLAN`) but not declared.
    - Assignment books one arm's shadow decision per fixture. It has no causal effect on the market, so this is randomized counterfactual-policy evaluation, not an intervention. Real interventions need paper execution routing, which is not built and is out of authority.
    - Power assumptions: sd 0.27 per unit, MDE 0.02, α 0.05/2, power 0.8, giving 3,465 per arm.
    - A design may be corrected only while REGISTERED and before `start_at` (60s after registration).
14. **Statistics are conservative.** Bonferroni rather than Holm or FDR. Fixed-sample tests with no sequential alpha spending.
15. **No periodic refit.** Training is attempted at most every 6h until a subject registers. A version is re-registered only when the approved threshold changes or the EDDIE interface appears. Models therefore age, and a refit cadence (a new version with a new family plan) is follow-up work.
16. **Bounds.** 180-day scoring window, 20,000-row read limits, 500 captures per cycle, 5,000 training rows, 15-minute cycle, 120s per component, 20s statement timeout.
17. **The runner is armed ON by default, like `INTEL_SHADOW`, with `POS_LEARN=off` as the kill switch.** No environment was changed. Arming takes effect only when this branch is deployed, and nothing has been deployed.
18. **Command Center: endpoints only.** No frontend page was built.
