CLAUDE — INTEGRATE `BETTOR_PM_EVIDENCE_ACCEPTANCE_PACK_V1.zip` AS THE INDEPENDENT
EVIDENCE / PROFITABILITY / PM ACCEPTANCE LAYER FOR THE CURRENT BETTOR CLOSEOUT.

This is implementation work. Do not reply with only a plan.

The package has three required components and 45 baseline tests:

1. Golden Market Validation Pack — 18 tests
2. Forward Profitability Scoreboard — 11 tests
3. Independent Final PM Acceptance Harness — 16 tests

## A. VERIFY PACKAGE BASELINE FIRST

Unpack it unchanged and run all three suites.

Expected:
- Golden: 18/18
- Profitability Scoreboard: 11/11
- PM Acceptance: 16/16
- Total: 45/45

Then integrate into the actual current BETTOR tree.

Do not fork parallel ledgers or duplicate existing mapping/profitability systems.

## B. GOLDEN MARKET VALIDATION — MAKE THIS A RELEASE GATE

Bind `golden_market_validation` to the current mapping / settlement / routing
code.

Required production behaviors:

- titles never authorize a money-path map
- structured sport/league/event/start/team identity required
- period/family/line/subject exactness required
- competition mismatch refuses
- eBattles cannot map to real EPL from matching team names
- team total cannot map to game total
- alternate line cannot map to another line
- economic alias equality requires complete payoff-vector equality
- unknown settlement state refuses
- three-way Team B NO is not Team A YES when draw state changes payoff
- explicit YES/NO routes remain independently priced
- best route is minimum all-in executable cost after fees/depth/slippage
- stale cheap route cannot win
- same-venue and cross-venue arb are both scanned

Preserve source provenance. The existing PMUS cases are captured venue evidence.
The current Kalshi cases are frozen semantic integration cases, NOT captured
production payloads.

When Matt forwards the Kalshi rep response / real API examples:
- add them as NEW source records
- preserve the current cases
- do not silently replace history
- add exact production-wire YES/NO regression cases

Golden validation must become required CI for any mapping/venue/settlement
change.

## C. FORWARD PROFITABILITY SCOREBOARD — FREEZE BEFORE NEW RESULTS

Bind `forward_profitability_scoreboard` into the existing profitability
warehouse/read models.

Do not change `thresholds.json` after the new forward cohort begins without
creating a new version.

Required mechanism separation:

- DIRECTIONAL
- SAME_VENUE_ARB
- CROSS_VENUE_ARB
- ROUTING_SAVINGS
- EXECUTION_ALPHA
- ALLOCATION_ALPHA
- XAVIER_MANAGEMENT

Every mechanism must show:
- decision rows where applicable
- independent canonical events
- expected P&L
- realized P&L
- residual
- lower-bound forward P&L per independent event
- capital-hours
- $/capital-hour
- settlement completeness
- fee completeness
- freshness completeness
- lifecycle/status

### Routing
Every routed claim should record:
- canonical claim
- chosen alias/venue/side
- chosen all-in cost
- next-best route
- savings vs next-best
- qty
- why losing routes lost

Track explicitly:
- how often Kalshi NO beats equivalent Kalshi YES
- how often Kalshi beats PMUS
- how often PMUS beats Kalshi
- total routing savings

### Adriana
Keep separate:
- same-venue opportunity count/P&L
- cross-venue opportunity count/P&L
- target quantity
- matched quantity
- full-match rate
- execution-locked count
- settlement proof
- fee completeness
- synchronized-book proof

Do not count theoretical arb as realized profit.

### Directional
Current historical negative receipt stays immutable.

Directional promotion still requires:
- >=100 independent forward events under current default threshold
- event-clustered validation
- untouched holdout
- market-prior OOS outperformance
- positive executable-EV lower bound
- positive forward P&L lower bound

### Accounting identity
Mechanism P&L + settlement adjustment + outcome variance must reconcile to the
existing PAPER ledger exactly within the frozen tolerance.

Do not create a new P&L truth.

## D. INDEPENDENT FINAL PM ACCEPTANCE HARNESS

Bind `final_pm_acceptance_harness` to machine-verifiable production evidence.

It produces:
RED
YELLOW
GREEN

### RED
Any critical integrity/software/runtime/safety failure:
- candidate not descendant of accepted production lineage
- tested/release/deployed SHA mismatch
- any of four exact-SHA gates red
- migration integrity red
- OOM/RSS gate red
- UMP still starts in shared workers / dedicated plane absent
- held freshness <95%
- priority freshness <95%
- truth quorum/reconciliation red
- SOFTWARE RED count >0
- canary red
- credential-class mismatch
- historical PAPER mutation
- live authority expanded during proof

### YELLOW
Critical software/operations green, but evidence still incomplete:
- PMX gRPC not primary
- Kalshi current market data not live/mapped
- venue health not isolated
- settlement proof incomplete
- Twin <95% / optimistic false fills too high
- Xavier completeness <95%
- positive forward edge lower bound not proven
- positive capacity not proven
- profitability governor still CASH
- active mechanism breaker not green

YELLOW remains PAPER_SHADOW_ONLY.

### GREEN
All critical AND economic gates pass.

GREEN means:
`CAPITAL_CANDIDATE`

It does NOT itself activate live money.

## E. EVIDENCE MUST COME FROM MACHINE SOURCES

Do not populate PM acceptance from a prose status message.

Use:
- GitHub exact-SHA CI / ancestry
- Render deploys/events/metrics/logs
- production Command APIs
- venue account positions/balances
- Audrey reconciliation
- current profitability/Twin/market-plane receipts

Every rate must have numerator + denominator.
Every metric must have `as_of` + source.
Missing evidence is not success.

## F. CURRENT ARCHITECTURE WORK MUST CONTINUE

This pack does not replace the completion-readiness, Kalshi canonical, or
red-team packages already assigned.

Continue to close:
- dedicated market plane
- one full-universe PMX gRPC subscribe-all stream
- source-level UMP isolation
- Kalshi first-class current-book path
- canonical YES/NO routing
- same/cross-venue Adriana
- truth-source quorum
- Twin repair
- runtime stability

## G. NO AUTHORITY EXPANSION

During this integration:
- SMALL LIVE = SHADOW
- KALSHI LIVE MONEY = NOT ACTIVATED
- ADRIANA = SHADOW_ONLY
- historical PAPER = immutable
- no risk/freshness/settlement/profitability gate weakening

## H. REQUIRED CHECKPOINT

Return proof only:

1. clean final branch/worktree
2. accepted production base SHA
3. new implementation SHA
4. ancestry proof
5. exact files changed
6. Golden 18/18
7. Scoreboard 11/11
8. PM Acceptance 16/16
9. existing mapping/Kalshi/Adriana tests
10. existing profitability/Twin tests
11. exact-SHA backend-tests
12. capital-critical
13. commit-guard
14. engine-diagnostic
15. Golden production receipt with per-case pass/refusal
16. forward scoreboard production readback
17. independent event denominators
18. routing savings receipt
19. same-venue arb receipt
20. cross-venue arb receipt
21. mechanism-specific expected vs realized residuals
22. exact P&L reconciliation
23. PM harness input receipt
24. PM harness result: RED/YELLOW/GREEN with exact blockers
25. deployed SHA / Render receipts
26. held freshness numerator/denominator
27. priority freshness numerator/denominator
28. PMX source counts
29. Kalshi mapping/current-book counts
30. Twin agreement / false-fill / lookahead receipt
31. Xavier completeness
32. SOFTWARE RED count
33. current PAPER P&L
34. current profitability governor
35. proven positive capacity
36. unresolved owner actions
37. explicit:
   SMALL LIVE = SHADOW
   KALSHI LIVE MONEY = NOT ACTIVATED
   ADRIANA = SHADOW_ONLY
   HISTORICAL PAPER = UNCHANGED
   NO AUTHORITY EXPANDED

Do not grade your own work with optimistic substitutions. If evidence is
missing, the PM harness must remain RED or YELLOW by rule.
