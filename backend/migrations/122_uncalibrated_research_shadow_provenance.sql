-- A POSITION CREATED UNDER THE RESEARCH WAIVER SAYS SO, DURABLY.
--
-- `rn1x_provenance_declared` enumerated three origins:
--
--     RN1_SIGNAL_DERIVED                     the historical/prospective lanes
--     ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY    the labelled acceptance harness
--     AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW
--                                            an entry that cleared every gate
--
-- The unfunded research lane needs a FOURTH, because an entry created while
-- MODEL_TRUST_DRIFT is still NOT_EVALUABLE is not the same claim as one that
-- cleared it. Writing such a position under the existing autonomous label
-- would make the two indistinguishable in every later read -- the P&L
-- display, the census, any calibration evidence built from them -- and the
-- difference is precisely what a reader needs.
--
--     UNCALIBRATED_RESEARCH_SHADOW           owner-authorised, unfunded,
--                                            calibration explicitly
--                                            unmeasured
--
-- THE CONSTRAINT IS WIDENED, NOT DROPPED. A position still cannot carry an
-- undeclared provenance, so a typo is refused rather than stored.
--
-- NOT VALID, deliberately and for the same reason migration 113 used it:
-- existing rows predate the column and legitimately carry NULL. Validating
-- would require inventing an origin for them.

BEGIN;

ALTER TABLE rn1x_positions
    DROP CONSTRAINT IF EXISTS rn1x_provenance_declared;

ALTER TABLE rn1x_positions
    ADD CONSTRAINT rn1x_provenance_declared
    CHECK (provenance IS NULL
           OR provenance IN ('RN1_SIGNAL_DERIVED',
                             'ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY',
                             'AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW',
                             'UNCALIBRATED_RESEARCH_SHADOW'))
    NOT VALID;

COMMENT ON COLUMN rn1x_positions.provenance IS
    'Where this position came from. RN1_SIGNAL_DERIVED: the management '
    'lanes. ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY: the labelled acceptance '
    'harness, synthetic and unfunded. '
    'AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW: an autonomous entry that '
    'cleared every gate including MODEL_TRUST_DRIFT. '
    'UNCALIBRATED_RESEARCH_SHADOW: an autonomous entry created under the '
    'owner-authorised unfunded research waiver, with the source calibration '
    'EXPLICITLY NOT ESTABLISHED. The last two are different claims and must '
    'never be summed as one.';

COMMIT;
