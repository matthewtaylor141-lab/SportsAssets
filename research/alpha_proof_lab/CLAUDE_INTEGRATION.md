# CLAUDE INTEGRATION DIRECTIVE

Do not merge this package into production execution in one step.

## Phase A — Research-only import

Create a new additive research namespace, for example:

`research/alpha_proof_lab/`

or package it under the existing research stack.

Rules:
- zero order authority
- zero cancel authority
- zero funded-account reads
- zero changes to existing risk/freshness/settlement gates
- immutable historical PAPER ledger
- SMALL LIVE remains SHADOW

## Phase B — Shadow decision receipts

For every evaluated candidate, persist:
- market prior probability
- model raw probability
- calibrated probability
- calibration segment
- expected fill price
- fill probability
- fees
- spread
- slippage
- adverse selection
- management cost
- correlation/risk charge
- earned rebate actually attributable
- net EV per contract
- conservative lower-bound EV
- capital status
- model card hash
- source data hash

No missing cost may silently default to zero unless the venue contract explicitly
has zero cost and the provenance is recorded.

## Phase C — Verification

Required before capital eligibility:
- chronological walk-forward receipt
- untouched holdout receipt
- forward SHADOW sample
- expected vs realized residual report
- calibration report by sport x family x regime
- CLV distribution
- PBO / multiple testing report
- Deflated-Sharpe-style report
- capacity frontier
- correlation concentration report

## Phase D — Paper integration

Only after research evidence:
- integrate the all-in EV gate before PAPER allocation
- `net_ev <= 0` => CASH_WAIT / SHADOW
- `UNMEASURED` => cannot justify growth
- no positive eligible recipient => CASH
- capacity is a ceiling, not a daily turnover target
- ACTIVE_CHAMPION requires absolute positive forward net $/capital-hour

## Acceptance language

Never say "profitable" solely because a backtest is positive.

Allowed:
- "positive historical walk-forward net expectancy"
- "positive forward shadow expectancy"
- "capital eligible under current evidence"

Only use "verified profitable" after settled forward evidence supports it and
expected-vs-realized residuals remain inside the predeclared acceptance bounds.
