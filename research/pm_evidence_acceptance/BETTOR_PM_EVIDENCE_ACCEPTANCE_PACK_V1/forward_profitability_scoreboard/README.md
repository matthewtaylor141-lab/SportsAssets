# 2 — Forward Profitability Scoreboard

This freezes the questions we will ask *before* the upgraded system generates
forward results.

The scoreboard deliberately separates:
- directional alpha
- same-venue arbitrage
- cross-venue arbitrage
- routing savings
- execution alpha
- allocation alpha
- Xavier management alpha

A portfolio-wide positive number is not allowed to hide a failing sleeve.

## Required bottom-line identity

Mechanism contributions + settlement adjustment + outcome variance must
reconcile exactly to reported PAPER P&L within the frozen tolerance.

## Critical denominators

Always report:
- decision rows
- independent canonical events
- independent settled markets where distinct from events

Promotion is based on independent events, never repeated decision rows.

## Threshold policy

`thresholds.json` is frozen before the new forward cohort. Changing a threshold
after seeing the result requires a new version and cannot retroactively promote
the old cohort.
