# BETTOR RED TEAM CLOSEOUT V1

This package converts the full PM red-team review into executable fail-closed
interlocks and an integration directive.

## Baseline
- 50 red-team failure modes mapped to named controls
- 51 package tests
- no network access
- no credentials
- no order submission
- no cancel authority
- no capital authority

## Core controls
- canonical economic exposure lock
- two-leg execution sentinel
- independent truth-source quorum
- mechanism-specific profit circuit breakers
- per-venue health isolation
- exact-SHA / ancestry release guard
- versioned fee evidence guard
- settlement-rule fingerprint invalidation
- Digital Twin certification
- independent-event / holdout integrity
- positive capacity frontier
- stale-UI truth gate
- correct Karen false-block economics
- additive P&L attribution
- credential-class guard
- stream reconnect/snapshot currency gate
- forward-only migration guard
- final capital-readiness interlock

## Dependencies included
The ZIP vendors the earlier:
- BETTOR Completion Readiness Patch V1
- BETTOR Kalshi Canonical Venue V1

## Critical distinction
This ZIP addresses every red-team finding in code/design and tests. It does not
make those controls live merely by existing. Production closure requires Claude
to integrate them into the current BETTOR tree and produce the exact-SHA,
runtime, chaos, reconciliation and forward-economics receipts specified in
`CLAUDE_DIRECTIVE.md`.

No package can honestly turn negative alpha evidence into positive alpha.
Instead, the final readiness gate keeps capital in CASH/PAPER-SHADOW until
positive independent forward economics actually exist.
