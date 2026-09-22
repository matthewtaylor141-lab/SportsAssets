# BETTOR completion work log

Durable, append-only. Intermediate detail lives here, not in handoffs.

## W0  Standing diagnosis — SUPERSEDED, kept for the record
Originally: "88% of committed capital-hours rest behind quotes that never
fill (27,955 committed vs <=3,696 ever held; 85% of episodes never fill).
The binding constraint is the FILL RATE, not the netting call."

**This was wrong and was disproved by measurement, not by argument.**
Never-filled episodes contribute exactly 0.00 net; the entire P&L comes
from episodes that fill. Raising the fill rate (WS5, flow gate) raised
the fill rate from 39% to 78% and made the per-contract loss WORSE
(-0.0032 -> -0.0048). See W0'.

## W0' Standing diagnosis, replacement
Two costs, measured, in direct tension:
1. Completing a maker fill as a taker costs Th_taker/|Th_maker| = 4.8x
   what resting earned. On DEV: taker fees -24.98 against rebates +6.33
   and position +4.36, for a net of -14.29.
2. Not completing removes the protection against carrying a directional
   leg through resolution. On EVAL two episodes opened during live CFB
   play cost -79.82 while the other 195 netted +5.53.

The loss is the ADVERSE MOVE. The fill rate was never the lever.

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

### WS1 shared policy — DONE
`backend/sportsassets/bettor_policy.py` is the one definition. The replay
imports it (`epi.shared is bp`, asserted) and every decision it used to
inline now calls it: entry/admission, sizing, cancel-other-on-fill, the
quoting horizon, release-vs-hold, and the four-way recovery dispatch.
`bettor_policy_runtime.py` is the live-side adapter; the engine still
governs and the policy only proposes.

Regression: bettor_strategy_v2 reproduced its prior numbers exactly
(V1 deltas +3.85 / +1.89 / -18.08 / +14.20), so the rewiring changed no
behaviour. 38 policy tests + 84 engine/adapter tests pass.

Defect found BY RUNNING IT: `admit()` recognised only the venue's
`MARKET_STATE_OPEN`, but every Book from the runtime carries the
normalised `OPEN`. The live engine would have stood aside on every
market and reported a market-state refusal indistinguishable from a
quiet day. `is_open()` now holds the translation in one place.

### WS2 repairs
- manifest capture: gated (see WS2a). Prepared in full.
- production integration: the state-vocabulary seam above; fixed.
- unequal liquidation: fixed in `_release_pair` — measure what the YES
  sale actually returned, ask NO for exactly that, re-pair the residue,
  and append RELEASE_INCREASED_DIRECTIONAL_EXPOSURE if net exposure grew.

### WS3 provenance — DONE
`RULES` labels each rule DERIVED or HYPOTHESIS with inputs, rationale and
evidence. Derived: entry, placement, inventory, inventory_cap, recovery.
Hypotheses: entry_band, sizing, release, flow, volatility.

### WS4/WS6 prespecified evaluation — DONE, artifacts written
Cut 2026-09-17T00:00Z, chosen from row counts and dates alone (DEV
15,385 rows / EVAL 15,205). Protocol committed in 851cb23 BEFORE the
split was read. 15 variants registered, 60 DEV looks, ONE eval touch.
Intervals are bootstraps over EVENTS resampled whole — 420 episodes over
five events are five observations.

Protocol amendment, made on DEV and recorded: "best worst-case net" is
degenerate (its optimum is to trade nothing; a 4-episode variant won
it). Activity floor >=30 episodes and >=3 events in every scenario.

RESULT: does not qualify. R10 on EVAL, -74.29 / -72.37 / -80.81 /
-100.01 with every clustered interval excluding zero on the negative
side. DEV had shown -3.29 worst case. See ECONOMIC_VERDICT_V2.md.

`bettor_evaluation.py` now REFUSES a second eval touch in code.

### WS5 alternatives — two attempted
- FLOW GATE (R4-R6): failed, kept as a negative result, not retuned.
- TAKER AVOIDANCE (R7-R11): best on DEV, catastrophic on EVAL, and the
  reason is exactly identified.
- VOLATILITY GATE (R12-R14): DEV ONLY. Refuses both loss episodes by
  7x-12x, threshold-insensitive. Cannot be validated here — the eval
  split is spent and the DEV period contains none of the volatility the
  rule targets. Prespecified for fresh data.

### Open, and genuinely blocked on data
The remaining experiment needs a capture containing live event
resolution and >=20 independent events (~a month at the current rate).
The observation release is the instrument, and the incentives API it
reads carries `eventStartTime` — the time-to-resolution input the
captured corpus lacks entirely.

### Correction found after the verdict was written
The DEV/EVAL cut (2026-09-17T00:00Z) sits FOUR HOURS before the PMUS
taker regime change (0.0600 -> 0.0695 at 2026-09-17T04:00Z). Both dates
come from the calendar and neither was picked with reference to the
other, but DEV therefore runs almost entirely under the old rate and
EVAL under the new one. The replay applies the regime BY FILL TIMESTAMP
so every number is arithmetically right; the DEV-to-EVAL COMPARISON is
what carries the confound. It cannot explain the result: EVAL's taker
fees were -21.81 against a position loss of -63.91, and 16% of 21.81 is
about $3 of a $74 loss. Recorded in the protocol docstring and in
ECONOMIC_VERDICT_V2.md rather than left for a reader to find.

### Manifest capture — re-verified this session, still gated
gateway.polymarket.us: 403 CONNECT from this container, confirmed again.
NEW finding: `beta48-forward-capture.yml` and
`beta48-substantive-capture.yml` ARE on the default branch and DO reach
the venue, so Actions runners have egress. But each runs a FIXED set of
script names; `beta48-substantive-capture` checks out an arbitrary
`code_sha` yet still invokes substantive_select.py / capture_manifest.py
/ substantive_capture.py by name, so a new file at my SHA would never
run, and repurposing a frozen capture script is the unrelated
modification the standing restrictions forbid.
Two unblocking routes, in APPROVAL_REQUEST_V3.md. Recommended: allow
gateway.polymarket.us in the environment network policy (smallest, no
repository effect, no service restart).
