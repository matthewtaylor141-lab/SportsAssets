"""WHAT IDENTIFIES A CAPACITY-HARNESS RECORD. One definition, in the image.

WHY THIS FILE EXISTS, AND THE DEFECT IT CLOSES. The audit endpoint imported
this from `backend/tools/`, which the Dockerfile does not copy into the
image -- so the route worked in the repository and returned 500 in
production, and the production isolation question stayed unanswered. The
definition belongs in the PACKAGE, where both the endpoint and the
command-line tool can import it.

AND IT IS A FINGERPRINT, NEVER AN EXPERIMENT ID. The harness's writer
phases once stamped the autonomous strategy's experiment id on their
synthetic plans; identifying its records by that id would identify the
defect and miss the records. What identifies them is what the harness
itself writes and nothing else does: the shape of the condition id, the
slug prefix and the payout event.
"""

from __future__ import annotations

#: The two books the harness may write under. Anything else is a refusal,
#: enforced before its first write.
OWN_EXPERIMENTS = ("CAPACITY_PROBE_WRITER_V1", "CAPACITY_PROBE_LIFECYCLE_V1")

#: The books a synthetic capacity record may never be filed under.
STRATEGY_EXPERIMENTS = ("EXT_PINNACLE_DEVIG_V1_SHADOW",
                        "RN1X_SHADOW_CHALLENGER_HOLD_RANKED_V1",
                        "RN1X_SHADOW_PROSPECTIVE_V1",
                        "RN1X_SHADOW_HISTORICAL_V1")

#: A SQL predicate over `rn1x_positions p`. Every clause is something the
#: harness writes by construction:
#:
#:   condition_id      0x + 64 hex, ending c0de**** (writer phases) or
#:                     cafe**** (the complete-lifecycle phase)
#:   venue_market_slug aec-cap-%05d-... / aec-lifecycle-%05d-...
#:   payout_event      "Capacity Probe ..." / "Capacity Lifecycle ..."
FINGERPRINT = """
    (p.condition_id LIKE '0x%c0de0000'
     OR p.condition_id LIKE '0x%cafe0000'
     OR p.condition_id ~ '^0x0+c0de[0-9a-f]{4}$'
     OR p.condition_id ~ '^0x0+cafe[0-9a-f]{4}$'
     OR p.venue_market_slug LIKE 'aec-cap-%'
     OR p.venue_market_slug LIKE 'aec-lifecycle-%'
     OR p.payout_event LIKE 'Capacity Probe %'
     OR p.payout_event LIKE 'Capacity Lifecycle %')
"""

#: The counts an audit returns, as one statement. `$1` is the strategy
#: experiment list. It READS ONLY -- an audit that could write would be a
#: worse problem than the one it is auditing.
AUDIT_SQL = """
WITH probe AS (
  SELECT p.position_id, p.experiment_id, p.condition_id
    FROM rn1x_positions p WHERE %s
)
SELECT
  (SELECT count(*) FROM probe)                                 AS positions,
  (SELECT count(*) FROM probe WHERE experiment_id = ANY($1::text[]))
                                                               AS in_strategy_book,
  (SELECT count(*) FROM rn1x_orders o
    WHERE o.position_id IN (SELECT position_id FROM probe))     AS orders,
  (SELECT count(*) FROM rn1x_fills f JOIN rn1x_orders o
     ON o.order_id = f.order_id
   WHERE o.position_id IN (SELECT position_id FROM probe))      AS fills,
  (SELECT count(*) FROM rn1x_decisions d
    WHERE d.position_id IN (SELECT position_id FROM probe))     AS decisions,
  (SELECT count(*) FROM rn1x_outcomes x
    WHERE x.position_id IN (SELECT position_id FROM probe))     AS outcomes,
  (SELECT count(*) FROM external_valuations e
    WHERE e.condition_id IN (SELECT condition_id FROM probe))   AS valuations,
  (SELECT count(*) FROM rn1x_positions)                         AS all_positions
""" % FINGERPRINT

BOOKS_SQL = """
    SELECT experiment_id, count(*) AS n FROM rn1x_positions
     GROUP BY 1 ORDER BY 2 DESC LIMIT 40
"""


def describe() -> dict:
    """The audit's own contract, for a reader of its output."""
    return {
        "identified_by": ("the harness's own fingerprints -- the condition "
                          "id shape, the slug prefix and the payout event "
                          "-- and NEVER by an experiment id"),
        "own_experiments": list(OWN_EXPERIMENTS),
        "strategy_experiments": list(STRATEGY_EXPERIMENTS),
        "read_only": True,
        "an_unreadable_audit_is_not_an_empty_one": True,
        # WHAT THE COUNTS DO AND DO NOT ESTABLISH. The positions count is
        # the load-bearing one: positions and fills feed exposure and
        # per-lane P&L DIRECTLY, so zero valuation rows would prove nothing
        # about either -- no consumer of exposure or P&L reads
        # external_valuations on the way to a position. The evidence that
        # nothing reached those consumers is zero IDENTIFIED PROBE POSITIONS
        # together with zero of their dependent records.
        "the_load_bearing_count": "positions",
        "what_the_valuations_count_is": (
            "one more consumer that saw nothing -- the calibration sample's "
            "input. It is reported beside the others, NOT as the proof: a "
            "probe position with its orders and fills would have entered "
            "exposure and P&L with no valuation row in sight"),
        "the_isolation_evidence": (
            "zero identified probe positions AND zero dependent records "
            "(orders, fills, decisions, outcomes) -- enumerated from the "
            "positions downwards"),
    }
