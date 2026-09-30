-- ══════════════════════════════════════════════════════════════════════
-- 153 · DEREK: ONE RECORDED ENTRY DECISION PER EVALUATED CANDIDATE, AND A
--       MEASURED CENSUS OF THE CATALOGUE HE IS DECIDING OVER
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT THIS RECORDS. Derek is the discovery/initial-entry agent. His policy
-- (DEREK_ENTRY_POLICY_V1, `sportsassets.agents.derek_policy`) is:
--
--   CONSERVATIVE AGREEMENT. The de-vigged Pinnacle probability AND the
--   approved internal model's probability must EACH clear
--       qualified_probability - executable_acquisition_price >= min_gross_edge_pp
--   (min_gross_edge_pp = 0.05 PROBABILITY POINTS on a $0/$1 contract -- NOT a
--   5% return on capital), and the expected NET profit after the deployed fee
--   schedule (bettor_funded_book.fee_for, at the exact price and quantity)
--   must be positive (and at least min_net_ev_usd, default 0).
--
-- `derek_entry_decisions` is an INDEX OF DECISIONS, NOT A SECOND BOOK. It
-- links to the authoritative `external_valuations` row (`valuation_id`) and
-- copies only the fields the owner asked to see side by side: each estimate
-- with its timestamp and qualification, the gross edge in probability points,
-- the expected profit after fees and the expected return on deployed capital,
-- the named pre-purchase checks and the verdict. Nothing here is accounting:
-- fills, fees paid and realised P&L stay in bettor_funded_* and are read from
-- there.
--
-- APPEND-ONLY. A decision is a record of what was known at `decided_at`; a
-- later read that disagrees is a NEW decision (a different policy version or
-- valuation), never an edit. The trigger refuses UPDATE and DELETE.
--
-- `features` / `feature_sha` are the decision-time vector for the internal
-- entry model (bettor_funded_model.KEY_ENTRY_PAYOUT). They are written BEFORE
-- the outcome exists, which is what lets the registry train and evaluate that
-- model on these rows prospectively. The label is read from the valuation's
-- own outcome join -- never copied here.
--
-- `derek_coverage_census` is one measured census of the configured Polymarket
-- US catalogue (`us_premap`) per scheduled cycle: listed, within the mandate,
-- supported, receiving current data, evaluated, blocked by reason, not yet
-- evaluated. Also append-only.
--
-- ADDITIVE. Two new tables; nothing existing is altered.

BEGIN;

CREATE TABLE IF NOT EXISTS derek_entry_decisions (
    decision_id               text PRIMARY KEY,
    valuation_id              bigint,
    fixture                   text,
    us_market_slug            text,
    side                      text,
    decided_at                timestamptz NOT NULL,
    policy_version            text        NOT NULL,
    -- THE TWO ESTIMATES, EACH WITH ITS OWN CLOCK AND QUALIFICATION.
    pinnacle_p                double precision,
    pinnacle_at               timestamptz,
    pinnacle_qualification    text,
    model_p                   double precision,
    model_version             text,
    model_at                  timestamptz,
    model_qualification       text,
    -- THE ECONOMICS, IN EXPLICIT UNITS.
    --   executable_price           $ per contract, depth-weighted over qty
    --   gross_edge_pp              probability points (0.05 = 5 pp), on the
    --                              LOWER of the two estimates
    --   expected_*_usd             dollars for `qty` contracts
    --   expected_net_roi           net / (acquisition cost + fees), a ratio
    executable_price          double precision,
    qty                       numeric,
    gross_edge_pp             double precision,
    expected_gross_profit_usd double precision,
    fees_usd                  double precision,
    expected_net_profit_usd   double precision,
    expected_net_roi          double precision,
    checks                    jsonb       NOT NULL,
    verdict                   text        NOT NULL,
    refusal                   text,
    latency                   jsonb,
    evidence                  jsonb,
    -- THE DECISION-TIME MODEL VECTOR (see header).
    features                  jsonb,
    feature_sha               text,
    decided_by                text        NOT NULL,
    recorded_at               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT derek_entry_decisions_verdict_ck CHECK (
        verdict IN ('ENTER', 'REFUSE')),
    --: A REFUSAL IS NAMED; AN ENTRY CARRIES NONE.
    CONSTRAINT derek_entry_decisions_refusal_ck CHECK (
        (verdict = 'ENTER' AND refusal IS NULL)
        OR (verdict = 'REFUSE' AND refusal IS NOT NULL AND refusal <> '')),
    --: AN ENTRY IS NEVER RECORDED WITHOUT BOTH ESTIMATES AND POSITIVE NET EV.
    CONSTRAINT derek_entry_decisions_enter_is_complete_ck CHECK (
        verdict <> 'ENTER'
        OR (pinnacle_p IS NOT NULL AND model_p IS NOT NULL
            AND model_version IS NOT NULL AND gross_edge_pp IS NOT NULL
            AND expected_net_profit_usd IS NOT NULL
            AND expected_net_profit_usd > 0
            AND fees_usd IS NOT NULL AND qty IS NOT NULL AND qty > 0)),
    CONSTRAINT derek_entry_decisions_decided_by_ck CHECK (
        decided_by IN ('FUNDED_ENTRY_GATE', 'AFTER_CYCLE'))
);

CREATE INDEX IF NOT EXISTS derek_entry_decisions_at_idx
    ON derek_entry_decisions (decided_at DESC);
CREATE INDEX IF NOT EXISTS derek_entry_decisions_valuation_idx
    ON derek_entry_decisions (valuation_id);
CREATE INDEX IF NOT EXISTS derek_entry_decisions_fixture_idx
    ON derek_entry_decisions (fixture, decided_at);
--: ONE DECISION PER VALUATION PER POLICY VERSION. The funded gate and the
--: after-cycle pass derive the same id, so whichever records first stands.
CREATE UNIQUE INDEX IF NOT EXISTS derek_entry_decisions_one_per_valuation
    ON derek_entry_decisions (valuation_id, policy_version)
    WHERE valuation_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS derek_coverage_census (
    census_id          text PRIMARY KEY,
    at                 timestamptz NOT NULL,
    categories         jsonb       NOT NULL,
    blocked_by_reason  jsonb       NOT NULL,
    sample             jsonb       NOT NULL,
    recorded_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS derek_coverage_census_at_idx
    ON derek_coverage_census (at DESC);

-- ── BOTH TABLES ARE RECORDS ─────────────────────────────────────────
CREATE OR REPLACE FUNCTION derek_record_is_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: a Derek record is what was known when '
                    'it was written, and a later read is a new record',
        TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS derek_entry_decisions_append_only_trg
    ON derek_entry_decisions;
CREATE TRIGGER derek_entry_decisions_append_only_trg
    BEFORE UPDATE OR DELETE ON derek_entry_decisions
    FOR EACH ROW EXECUTE FUNCTION derek_record_is_append_only();

DROP TRIGGER IF EXISTS derek_coverage_census_append_only_trg
    ON derek_coverage_census;
CREATE TRIGGER derek_coverage_census_append_only_trg
    BEFORE UPDATE OR DELETE ON derek_coverage_census
    FOR EACH ROW EXECUTE FUNCTION derek_record_is_append_only();

COMMENT ON TABLE derek_entry_decisions IS
    'Derek''s entry decisions under a versioned policy: an index linking to '
    'external_valuations (valuation_id), never a copy of the accounting. '
    'gross_edge_pp is in probability points on a $0/$1 contract.';
COMMENT ON TABLE derek_coverage_census IS
    'One measured census of the Polymarket US catalogue (us_premap) per '
    'scheduled cycle, unsupported markets kept visible by reason.';

COMMIT;
