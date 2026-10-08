# 3 — Independent Final PM Acceptance Harness

This harness is deliberately independent of Claude's own status narrative.

It produces exactly one PM state:

- **RED** — critical software/integrity/runtime/freshness/reconciliation/safety
  failure. Do not advance.
- **YELLOW** — code/operations can be healthy, but economic or evidence proof
  is incomplete. Remain PAPER/SHADOW.
- **GREEN** — critical system gates and forward economics are both green.
  System may be called `CAPITAL_CANDIDATE`. This still does not activate live
  money.

Missing evidence never defaults to success.

The evidence collection contract specifies which facts should come from
GitHub/CI, Render, production Command readbacks, and venue reconciliation.
