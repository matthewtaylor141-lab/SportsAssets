# CLAUDE DIRECTIVE — BETTOR RED TEAM CLOSEOUT V1

Integrate `BETTOR_RED_TEAM_CLOSEOUT_V1.zip` as the final adversarial safety /
truth / capital-readiness layer. This is an implementation directive, not a
planning exercise.

The package baseline is **51/51 tests GREEN**. It also vendors:
- `BETTOR_COMPLETION_READINESS_PATCH_V1.zip`
- `BETTOR_KALSHI_CANONICAL_VENUE_V1.zip`

The objective is to close every code-controlled red-team failure mode listed in
`RED_TEAM_CLOSURE_MATRIX.md`, without weakening current gates and without
activating live money.

## 0 — LINEAGE FIRST

Before integrating anything, prove the working branch is based on the accepted
production lineage.

Current warning:
`claude/completion-readiness-v1` @
`2bcdf10c8396a6ffa354c74b6a6ee7c574cb931d`
was observed as diverged from the previously tested closeout lineage.

Do not release a divergent or stale tree.

Create a clean successor branch from the current accepted production/release
tree, then bring in the already-reviewed completion-readiness changes and
Kalshi work deliberately.

Suggested:
`claude/red-team-closeout-v1`

Required proof before release:
- accepted base SHA
- merge/rebase strategy
- no accepted production commit lost
- `git merge-base --is-ancestor <accepted-base> <candidate>` equivalent proof
- final candidate is exact tree that CI and Render deploy

## 1 — PACKAGE BASELINE

Run the ZIP unchanged.

Expected:
`51/51 GREEN`

Preserve those tests as required CI.

## 2 — CANONICAL EXPOSURE LOCK

Integrate `bettor_red_team/claim_exposure.py`.

Allie/risk/capital must size **economic claims**, not raw venue instruments.

Equivalent aliases such as:
- Kalshi Yankees YES
- Kalshi Rays NO
- Polymarket Yankees YES

must contribute to ONE canonical claim exposure when the Kalshi canonical
package proves payoff equality.

Required:
- claim-level notional cap
- event-level notional cap
- alias list/readback
- equivalent aliases correlation = 1 for concentration purposes
- no strategy/agent/venue label can hide duplicate exposure

A trade that passes venue-level limits but breaches canonical claim/event limits
must be refused.

## 3 — ADRIANA TWO-LEG EXECUTION SENTINEL

Integrate `bettor_red_team/two_leg_sentinel.py` into Adriana's SHADOW execution
model first.

Keep separate concepts:

`GUARANTEED_AFTER_COSTS_IF_FILLED`

versus:

`EXECUTION_LOCKED`

Do not call a structure execution-locked until BOTH legs are confirmed at the
target matched quantity.

Required states:
- DISCOVERED
- REVALIDATED
- ARMED
- PARTIAL
- MATCHED
- LOCKED
- REPAIR_REQUIRED
- FROZEN

Required controls:
- revalidate books immediately before action
- revalidate settlement fingerprint immediately before action
- revalidate all-in economics immediately before action
- matched quantity separate from each leg's filled quantity
- unmatched quantity visible
- max unmatched loss cap
- partial-fill repair/freeze semantics
- overfill freezes
- ambiguous venue ack never causes blind retry
- deterministic client/order identity
- venue fills, not ack claims, drive position truth

Do not add live submit/cancel authority in this workstream.

## 4 — TRUTH-SOURCE QUORUM

Integrate `truth_quorum.py`.

Capital/readiness must require independent agreement among:
- INTERNAL_LEDGER
- VENUE_POSITIONS
- VENUE_BALANCE
- MARKET_DATA
- AUDREY_RECONCILIATION

Missing/stale source is not zero and is not green.

Any unexplained position quantity/value mismatch beyond the configured tolerance
must:
- block new capital
- preserve exit/reduce/protection authority
- surface exact claim/source mismatch

This is especially important before any future Small Live promotion.

## 5 — PROFIT-SOURCE CIRCUIT BREAKERS

Integrate `profit_breaker.py` with the profitability stack/lifecycle.

Evaluate mechanisms independently:
- structural/same-venue arb
- cross-venue arb
- directional model
- execution/maker alpha
- allocation alpha
- Xavier management alpha

A broken sleeve must be disabled/shadowed without forcing unrelated proven
sleeves off.

A good sleeve must never be used to hide the negative residual of another.

Required readback per mechanism:
- independent observations/events
- expected P&L
- realized P&L
- residual
- residual confidence bound
- cumulative residual
- state: ELIGIBLE / SHADOW_ONLY / DISABLED
- reason

## 6 — VENUE HEALTH ISOLATION

Integrate `freshness_guard.py` and `stream_guard.py`.

No blended "market healthy" status.

Maintain independent:
- POLYMARKET_US health
- KALSHI health

For cross-venue structures, BOTH must be current.

Connected socket != current book.

After reconnect:
- mark books GAP/STALE
- require complete authoritative replacement snapshot
- only then restore current status

Do not widen freshness SLAs to make the rate green.

Priority/held/global denominators remain separate. Capital uses held/priority
truth, not the 74k-universe average.

## 7 — FEES / ALL-IN ROUTING

Integrate `fee_guard.py` around existing fee truth.

Unknown fee = ineligible.

Every money-path fee record needs:
- venue
- market/series key
- schedule id
- version
- effective time/date
- maker known?
- taker known?

Do not assume maker fee zero unless the current venue/series evidence proves it.

Continue routing on TOTAL all-in executable cost, not displayed price.

## 8 — SETTLEMENT CERTIFICATE INVALIDATION

Integrate `settlement_guard.py`.

Every mapping/arb/trade must carry the rule fingerprint it was certified
against.

If current rule fingerprint differs:
`RULES_CHANGED_SINCE_CERTIFICATION`

and the mapping/arb must be re-established before new exposure.

Unknown payoff state or resolution-source difference remains fail-closed.

Do not infer compatibility from labels.

## 9 — DIGITAL TWIN CERTIFICATION

Integrate `digital_twin_gate.py` into capital readiness.

Current historical evidence (~70.1% fill agreement / optimistic IOC mismatches)
must remain negative baseline evidence.

Default certification:
- >=95% fill-state agreement
- optimistic false-fill rate <=1%
- zero lookahead violations
- mismatch taxonomy present

If the team adopts a different threshold, it must be statistically justified
and frozen before looking at promotion results.

Twin P&L cannot support capital while Twin is uncertified.

## 10 — INDEPENDENT SAMPLE / MODEL-SELECTION INTEGRITY

Integrate `sample_integrity.py`.

Always report:
- decision rows
- independent canonical events/settlements
- row:event ratio

Required:
- event-clustered train/test/holdout
- no event leakage
- holdout opened once
- candidate set preregistered
- no model/strategy selection after holdout
- PBO/DSR controls for strategy mining where applicable

Thousands of reevaluations of one game are not thousands of independent
samples.

## 11 — CAPACITY FRONTIER, NOT TURNOVER TARGET

Integrate `capacity_guard.py`.

Capital is capped by proven positive capacity.

If management target = $500k/day but proven positive capacity = $82k:
deploy <= $82k.

The remainder is CASH.

Required capacity evidence by mechanism/strategy:
- quantity
- lower-bound EV per contract
- fill probability
- expected net
- capital-hours
- marginal economics

Never linearly extrapolate a tiny PAPER trade to institutional size.

## 12 — KAREN ECONOMIC SCORE

Integrate `karen_value.py`.

Karen economic value is exactly:

`saved loss - false-block opportunity cost`

Do not score her by block count.

Add regression:
saved loss $100, false-block opportunity cost $30 -> value added **$70**.

## 13 — ADDITIVE P&L ATTRIBUTION

Integrate `attribution.py`.

One additive identity only:

selection
+ execution
+ allocation
+ management
+ settlement
+ outcome variance
= realized P&L

No agent may claim the same dollar twice.

Any residual outside tolerance keeps attribution/readiness red.

## 14 — CREDENTIAL CLASS GUARD

Integrate `credential_guard.py` concept at startup diagnostics using existing
secure shape/fingerprint logic.

Never log secret values.

Expected classes:
- PMX -> institutional RSA M2M
- PMUS -> retail Ed25519
- Kalshi -> Kalshi RSA API key

A credential in the wrong logical slot blocks that path and names the owner
action; do not weaken auth to make it work.

## 15 — MIGRATION / RELEASE TRUTH

Integrate:
- `migration_guard.py`
- `release_guard.py`

Applied migration history is immutable. Use forward migrations only.

Capital/readiness requires:
- accepted base ancestor of final candidate
- tested SHA == release SHA
- release SHA == deployed SHA
- backend-tests GREEN
- capital-critical GREEN
- commit-guard GREEN
- engine-diagnostic GREEN
- migration fingerprint match
- fresh-db migration suite GREEN

A package import SHA is not a release proof.

## 16 — UI TRUTH

Integrate `ui_truth.py` into Command readiness/profitability surfaces.

Every management metric that can influence capital must carry:
- value
- as_of
- source
- status
- numerator/denominator when a rate

A stale/missing metric can never remain visually GREEN because the last cached
value was green.

No demo fallback in production.

## 17 — FINAL READINESS INTERLOCK

Integrate `readiness.py` with the existing capital-readiness read model.

The final status remains:

`PAPER_SHADOW_ONLY`

unless ALL are true:
- release lineage/exact-SHA green
- runtime stable
- market data current
- held freshness green
- priority freshness green
- settlement green
- Digital Twin certified
- ledger/venue/Audrey reconciliation green
- positive independent edge proven
- positive capacity proven
- canonical exposure gate green
- all active profit-source breakers green
- Small Live / new authority remains shadow during proof
- historical PAPER immutable

Only then:
`CAPITAL_CANDIDATE`

This still does not itself activate real money.

## 18 — DATABASE EVIDENCE

Use `sql/314_red_team_closeout.sql` as an additive template. Renumber if that
migration number is already occupied.

The new evidence tables are append-only. Do not create a second trading ledger.
They store safety/readiness receipts only.

## 19 — CHAOS / ADVERSARIAL ACCEPTANCE

Execute every scenario in `CHAOS_ACCEPTANCE_MATRIX.md`.

At minimum automate C01-C30 where feasible.

Acceptance is not "no exception."

Acceptance means:
- new risk fails closed
- existing positions are preserved
- exit/reduce/protection remain available when appropriate
- blocker is named
- no profit is invented
- state reconciles after recovery

## 20 — EXISTING SAFETY MUST REMAIN

Do not:
- activate Kalshi live money
- activate BETTOR Small Live
- grant Adriana submit/cancel/capital authority
- grant Archer new authority
- grant Allie direct order authority
- weaken freshness
- weaken settlement
- weaken profitability
- change credentials without owner action
- rewrite historical PAPER
- overwrite negative research receipts
- tune on holdout
- force turnover
- call unexecuted arb "locked"

## REQUIRED TESTS

Run:
1. package tests — expected 51/51
2. completion-readiness package tests
3. Kalshi canonical package tests
4. current Kalshi mapping/venue/isolation tests
5. Adriana arb tests
6. capital authority tests
7. profitability bind/stack tests
8. Twin tests
9. freshness/market-plane tests
10. settlement tests
11. agent scorecard tests
12. Xavier management tests
13. fresh DB migrations
14. release/commit guards

Then exact final SHA:
- backend-tests
- capital-critical
- commit-guard
- engine-diagnostic

## NEXT ACCEPTABLE CHECKPOINT — PROOF ONLY

Return:
1. final clean branch/worktree
2. accepted base SHA
3. NEW implementation SHA
4. ancestry proof
5. exact files changed
6. package 51/51
7. completion-readiness tests
8. Kalshi canonical tests
9. canonical exposure receipt
10. two-leg sentinel receipt
11. venue health isolation receipt
12. truth quorum receipt
13. profit-source breaker receipt
14. settlement fingerprint receipt
15. Twin certification receipt
16. independent-event/sample-integrity receipt
17. capacity frontier receipt
18. Karen value-add regression
19. attribution reconciliation receipt
20. credential-class receipt
21. migration fingerprint/fresh DB receipt
22. chaos acceptance report C01-C30
23. backend-tests exact SHA
24. capital-critical exact SHA
25. commit-guard exact SHA
26. engine-diagnostic exact SHA
27. release SHA
28. Render/API/workers/market-plane deploy receipts as applicable
29. deployed SHA readback
30. production freshness denominators
31. production reconciliation
32. current PAPER P&L
33. current profitability governor
34. current capital-readiness status
35. exact unresolved OWNER_ACTION_REQUIRED items
36. explicit:
   - SMALL LIVE = SHADOW
   - KALSHI LIVE MONEY = NOT ACTIVATED
   - ADRIANA = SHADOW_ONLY
   - HISTORICAL PAPER = UNCHANGED
   - NO AUTHORITY EXPANDED

Do not declare a red-team item CLOSED from unit tests alone. Close it only when
the code is bound to BETTOR and the required integration/runtime receipt exists.
