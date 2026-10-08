# BETTOR PM Evidence & Acceptance Pack V1

A single package containing the three parallel workstreams needed while Claude
finishes the current completion / Kalshi / red-team implementation.

## 1. Golden Market Validation Pack
Frozen mapping and equivalence cases. It prevents the system from earning a
"mapping complete" label by merely matching strings or passing synthetic happy
paths.

Baseline: 18 tests.

## 2. Forward Profitability Scoreboard
Metrics and thresholds frozen before the upgraded forward cohort. It separates
directional alpha, same-venue arb, cross-venue arb, routing savings, execution,
allocation, and Xavier management.

Baseline: 11 tests.

## 3. Independent Final PM Acceptance Harness
A separate evaluator for exact SHA, lineage, CI, Render/runtime, market-data
health, freshness, reconciliation, Twin certification, profitability, and
capacity.

Baseline: 16 tests.

## Total baseline
45 tests.

## Important evidence distinction
The Polymarket golden cases are grounded in venue fixtures already captured in
the repository. Kalshi YES/NO cross-venue semantic cases are frozen integration
cases based on the current adapter/mapping semantics until real production
payloads arrive from Kalshi support/rep. Do not relabel them as captured
production evidence.

## Authority
This package grants no live trading authority and does not change credentials,
risk limits, or historical PAPER.
