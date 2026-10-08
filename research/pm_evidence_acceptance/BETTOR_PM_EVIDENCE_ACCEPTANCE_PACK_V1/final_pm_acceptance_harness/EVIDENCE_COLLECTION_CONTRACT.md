# Evidence collection contract

The PM harness is intentionally independent of Claude's narrative status.
Populate evidence from machine-verifiable sources.

## GitHub / release
- accepted base SHA
- candidate ancestry
- exact tested SHA
- exact release SHA
- all four exact-SHA gate conclusions
- fresh DB migration result / migration fingerprint

## Render
- deployed API SHA
- deployed worker SHA
- dedicated market-plane service presence
- worker OOM event history
- worker RSS high-water / memory limit
- shared-worker UMP start count
- dedicated market-plane boot/readback

## Command / production API
- held fresh / held required
- priority fresh / priority required
- SOFTWARE RED count
- canary verdict
- Xavier packet completeness
- PMX source counts
- Kalshi mapped/current market counts
- venue health separation
- settlement coverage
- Audrey reconciliation / truth quorum
- current PAPER P&L
- profitability governor
- proven positive capacity
- mechanism breaker states
- Twin compared/matched/false-fill/lookahead metrics

## Authority
- SMALL LIVE remains SHADOW during evidence period
- Kalshi live money is not activated
- Adriana remains SHADOW
- historical PAPER unchanged

Missing evidence is not success. The harness should remain RED/YELLOW rather
than fill missing fields with optimistic defaults.
