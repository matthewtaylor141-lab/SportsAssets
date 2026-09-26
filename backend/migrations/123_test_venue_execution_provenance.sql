-- A TEST-VENUE EXECUTION LIFECYCLE SAYS SO, DURABLY.
--
-- `rn1x_provenance_declared` enumerated four origins, and none of them
-- describes what the execution path writes:
--
--     RN1_SIGNAL_DERIVED                     the management lanes
--     ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY    the labelled acceptance harness
--     AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW
--                                            an entry that cleared every gate
--     UNCALIBRATED_RESEARCH_SHADOW           owner-authorised, unfunded,
--                                            calibration unmeasured
--
-- `bettor_test_venue_executor` drives submission, acknowledgement, partial
-- fill, cancellation and recovery against a TEST-class venue under a
-- validated authorization. Its rows are not a strategy entry and not a
-- chosen-input demonstration: they are the EXECUTION PATH being exercised.
-- Writing them under any label above would make an exercise of the order
-- lifecycle indistinguishable from a decision the strategy took, which is
-- exactly the distinction every later read depends on.
--
--     TEST_VENUE_EXECUTION_LIFECYCLE         an authorized order lifecycle
--                                            against a TEST-class venue;
--                                            never funded submission
--
-- THE CONSTRAINT IS WIDENED, NOT DROPPED, so an undeclared provenance is
-- still refused rather than stored.
--
-- NOT VALID, for the same reason as 113 and 122: existing rows predate the
-- column and legitimately carry NULL.

BEGIN;

ALTER TABLE rn1x_positions
    DROP CONSTRAINT IF EXISTS rn1x_provenance_declared;

ALTER TABLE rn1x_positions
    ADD CONSTRAINT rn1x_provenance_declared
    CHECK (provenance IS NULL
           OR provenance IN ('RN1_SIGNAL_DERIVED',
                             'ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY',
                             'AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW',
                             'UNCALIBRATED_RESEARCH_SHADOW',
                             'TEST_VENUE_EXECUTION_LIFECYCLE'))
    NOT VALID;

COMMENT ON COLUMN rn1x_positions.provenance IS
    'Where this position came from. RN1_SIGNAL_DERIVED: the management '
    'lanes. ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY: the labelled acceptance '
    'harness, synthetic and unfunded. '
    'AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW: an autonomous entry that '
    'cleared every gate including MODEL_TRUST_DRIFT. '
    'UNCALIBRATED_RESEARCH_SHADOW: an autonomous entry created under the '
    'owner-authorised unfunded research waiver, with the source calibration '
    'EXPLICITLY NOT ESTABLISHED. TEST_VENUE_EXECUTION_LIFECYCLE: the '
    'execution path exercised against a TEST-class venue under a validated '
    'authorization -- an exercise of the order lifecycle, NOT a decision the '
    'strategy took. None of these may be summed as one.';

COMMIT;
