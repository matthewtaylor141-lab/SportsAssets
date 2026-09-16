# §10 EVIDENCE SET — FROZEN BEFORE INSPECTION

Written and committed BEFORE either segment's economics were read. The git
history is the proof of ordering: if this file's commit does not precede the
§10 report's commit, the freeze is worthless and should be treated as such.

## THE TWO SEGMENTS

```
SEGMENT_8_ROLE = PRIMARY
  run 35048890640, dispatched 02:40:31Z, sealed 03:32:23Z
  head e68dd56, panel_per_stratum=8, panel_rounds=40
  selection LEXICOGRAPHIC_WITHIN_FROZEN_STRATA

SEGMENT_9_ROLE = TEMPORAL_REPLICATION
  run 35067690562, SCHEDULED (event=schedule), fired 07:16:26Z,
  completed 07:49:46Z, head efe1971
  selection LEXICOGRAPHIC_WITHIN_FROZEN_STRATA (workflow default)
```

Segment 8 is PRIMARY because it is the earlier of the two and because it was
dispatched deliberately as the §10 capture. Segment 9 is REPLICATION because it
fired on a schedule, four and a half hours later, and was not dispatched in
response to anything. Neither role was chosen after seeing a result.

**Why the order matters more than the reasoning.** Two panels and a free choice
of which is "primary" is a coin flip you get to call after it lands. Fixing the
roles by a rule that could have been stated in advance — earlier segment is
primary, scheduled re-run is replication — removes that.

## SEGMENT 9 IS NOT A SECOND CROSS-SECTION

```
SEGMENT_9_IS_INDEPENDENT_CROSS_SECTION = NO unless proven otherwise
```

Both panels use the same deterministic lexicographic rule over strata built
from the same programme facts, and the first such panel drew twelve markets
that were all UFC fights. The prior expectation is therefore heavy overlap, and
the burden is on the data to show independence rather than on the analyst to
assume it.

Where markets repeat, Segment 9 is **repeated temporal observations of the same
markets**, not new market evidence. Concretely:

- rows are NOT concatenated to double a sample size;
- inference clusters at market/event level;
- an overlapping market contributes one market to any cross-sectional count,
  however many times it was observed.

To be measured before anything economic is read:

```
SEGMENT8_UNIQUE_MARKETS =
SEGMENT9_UNIQUE_MARKETS =
MARKET_OVERLAP_COUNT =
MARKET_OVERLAP_RATE =
EVENT_OVERLAP_COUNT =
EVENT_OVERLAP_RATE =
```

## THE DECISION RULE

The gate is determined from the PRIMARY segment under the rules already frozen
in `DECISION_REPORT_TEMPLATE.md`. Segment 9 answers one question only:

```
DOES_THE_RESULT_REPLICATE_LATER_IN_TIME?
```

and both are reported side by side:

```
PRIMARY_RESULT =
TEMPORAL_REPLICATION_RESULT =
DIRECTIONALLY_CONSISTENT = YES/NO
ECONOMICALLY_CONSISTENT = YES/NO
MATERIAL_REGIME_DIFFERENCE = YES/NO/NOT_IDENTIFIED
```

Two rules that run in opposite directions, so neither segment can be used as an
escape hatch:

- **A favourable replication does not rescue a failed primary.** If the primary
  fails, the gate fails, and the replication is reported beside it as an
  unexplained inconsistency.
- **An unfavourable replication is not hidden by a passing primary.** It is
  reported at the same prominence.

If the two materially disagree, the correct gate is **BLOCKED** rather than a
choice between them. Selecting the agreeable one is the whole failure this
programme exists to avoid.

## PANEL SCOPE — UNCHANGED BY THERE BEING TWO OF THEM

```
PANEL_SPORT_SCOPE          = UFC
PANEL_GENERALIZES_TO_BOARD = NO
COVERAGE_BIAS              = YES
OUTCOME_LEAKAGE            = NO
```

Two UFC panels are not board validation. Running the same concentrated
selection twice measures the same corner of the board twice; it says nothing
about the rest of it. The cross-sectional generalization step is the salted-hash
panel, and it has not run.

## THE BLOCK GATE IS NOT WEAKENED

```
UNKNOWN_EXECUTION_TYPE != CLOB_EXECUTION
```

If exact block-row identification is unavailable: symbol-days with DMR block
volume > 0 stay contaminated; a missing DMR row stays unusable; unreconciled
volume stays unusable; only symbol-days clearing the gate may feed F0/F1/F2.
Total tape volume is not used as CLOB volume because the block share is
*believed* small — a belief about a magnitude is not a measurement of it.

No Time & Sales fill result is promoted until block contamination and
business-date reconciliation are resolved.
