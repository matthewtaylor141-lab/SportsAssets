# §10 DECISION REPORT — the required shape

The next substantive report is this one, unless another validity defect
intervenes. Fields are fixed in advance so the report cannot be shaped around
whatever the evidence turned out to say. Every line is answered or answered
`NOT_IDENTIFIED`; none is omitted.

## THE PROGRAMME ORDER THIS REPORT SERVES

```
A  finish clean public forward §10 evidence
B  determine whether STANDALONE maker economics survive:
     spread + maker rebate + verified incentives
     - adverse selection - residual inventory - exit cost
C  in parallel, pursue venue-enforced read-only institutional market data
D  when available: LEVEL_0 -> LEVEL_2, test gRPC unaggregated semantics,
     improve counterfactual fill / adverse-selection measurement
E  begin FIX onboarding in parallel as the eventual production-grade MBO
     source -- and do NOT block the current beta on it
F  actual BETTOR passive fills remain a LATER tiny-capital validation gate
```

B is the gate. C, D and E improve the measurement; none of them substitutes
for B, and none of them is B.

## BLOCK 1 — THE MAKER ENGINE

```
MAKER_ENGINE_GATE                =   PASS / FAIL / BLOCKED
TRADING_EDGE_EX_INCENTIVES       =
MAKER_REBATE_CONTRIBUTION        =
LIQUIDITY_INCENTIVE_CONTRIBUTION =
FILL_INCENTIVE_CONTRIBUTION      =
VOLUME_INCENTIVE_CONTRIBUTION    =
TOTAL_EXPECTED_NET               =
ADVERSE_SELECTION                =
RESIDUAL_INVENTORY_COST          =
OPPORTUNITY_FREQUENCY            =
CAPITAL_OCCUPANCY                =
CURRENT_PANEL_SCOPE              =
GENERALIZATION_STATUS            =
BIGGEST_REMAINING_UNKNOWN        =
SHORTEST_NEXT_EXPERIMENT         =
```

Rules binding this block:

- **The four incentive channels are never blended**, with each other or with
  trading edge. `ledger.py` refuses a credit whose basis does not match its
  channel, and the negotiated MM channel is a fifth line that can never be
  credited at all while its terms are `NOT_IDENTIFIED`.
- **`TRADING_EDGE_EX_INCENTIVES` is reported before, and separately from,
  `TOTAL_EXPECTED_NET`.** An engine that is negative ex-incentives and positive
  only with them is a subsidy-harvesting strategy, which may be a real business
  but is a different claim and must be labelled as one.
- **`BLOCKED` is a first-class answer.** If fill probability, queue position and
  adverse selection are still `NOT_IDENTIFIED`, the gate is BLOCKED, not FAIL
  and not PASS. A gate answered from terms that were never measured is worse
  than an unanswered gate.
- **`CURRENT_PANEL_SCOPE` and `GENERALIZATION_STATUS` travel with every number
  above them.** A UFC-local figure quoted without its scope becomes an
  exchange-wide figure the moment it is copied into a sentence.

## BLOCK 2 — THE DATA PATH

```
SAFE_REALTIME_PATH_FOUND                       =
BEST_SAFE_DATA_LEVEL_NOW                       =
BEST_SAFE_DATA_LEVEL_AFTER_INSTITUTIONAL_ACCESS =
FIX_PRODUCTION_PATH                            =
ACTUAL_BETTOR_FILL_EVIDENCE                    = NOT_IDENTIFIED
```

`ACTUAL_BETTOR_FILL_EVIDENCE` is pre-set to `NOT_IDENTIFIED` and stays there
until BETTOR submits real passive orders. No feed at any level changes it —
see `LEVEL_4_MBO_WITHOUT_BETTOR_ORDER` in `venue_capabilities.py`. It is not a
field to be filled in by better data.

## STANDING CONSTRAINTS ON THE REPORT ITSELF

No credentials. No connection. No orders. No capital. No production
activation. No Track A modification. No Phase X re-dispatch.
`mirror_live = false`.
