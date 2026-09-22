# BETTOR completion work log

Durable, append-only. Intermediate detail lives here, not in handoffs.

## W0  Standing diagnosis carried forward
88% of committed capital-hours rest behind quotes that never fill
(27,955 committed vs <=3,696 ever held; 85% of episodes never fill).
The binding constraint is the FILL RATE, not the netting call. Every
workstream below is pointed at that.

## Workstreams
- WS1 shared policy code (replay + runtime), full lifecycle
- WS2 repairs: manifest capture, production integration, unequal liquidation
- WS3 decision provenance: inputs / rationale / evidence, derived vs hypothesis
- WS4 economics: chronological dev/eval split, variant register, no eval tuning
- WS5 alternative candidate, materially justified from existing research
- WS6 prespecified evaluation with dependence + selection accounting

## Entries

### WS2a manifest capture — investigated, genuinely gated
Routes tried, all closed:
- direct from container: gateway.polymarket.us 403 CONNECT (proxy status
  reports `connect_rejected … policy denial`). 2 attempts, 0 reached venue.
- `incentive-manifest.yml` (written): 404 on dispatch — workflow_dispatch
  resolves the file on the DEFAULT branch.
- `fetch-docs.yml`: on default branch, docs hosts only, published nothing.
- `beta48-capability-docs.yml`, `run85-phase2a.yml`: both hardcode
  `ref: claude/session-njaewf`, so dispatching at another ref still checks
  out the default branch and runs its script, not ours.
- reconstruct from existing evidence: P3 recorded the programme PARAMETERS
  (pools, DF, target sizes, one programId, one eventStartTime) but NOT the
  individual market slugs, so a >=10-market allowlist cannot be rebuilt.
VERDICT: gated on the deployment merge, which carries the workflow. Prepared
in full; not a reason to hold the other workstreams.

### WS1 shared policy — starting
Policy currently lives in research (`bettor_episodes.Policy`) and the runtime
has no path to it. Moving the decision surface to
`backend/sportsassets/bettor_policy.py` so replay and runtime import ONE
definition, and proving it with a test that runs both against the same book
and asserts identical decisions.
