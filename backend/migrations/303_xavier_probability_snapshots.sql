-- 303: XAVIER'S PERSISTED PROBABILITY SNAPSHOTS (PAPER MANAGEMENT EVIDENCE).
--
-- THE DEFECT (ce8baa0 acceptance review): Xavier's management packet counted
-- the probability as present whenever its evidence state was
-- FRESH_CURRENT_PROBABILITY, whatever its provenance. Two of the three fresh
-- paths carried NO persisted valuation: the in-process PinnAPI feed reading
-- (paper_benchmark.xavier_measure, PINNAPI_FEED_CURRENT) and the two-model
-- CURRENT_BLEND (paper_xavier._measure, which read external_valuations but
-- dropped the row id). A management action could stand on a probability
-- nobody can audit back to a stored row.
--
-- THE RULE (xavier_packet.build): probability is packet-present ONLY when
-- the evidence state is FRESH_CURRENT_PROBABILITY AND a persisted
-- valuation_id is non-null. CURRENT_BLEND now carries its
-- external_valuations id; a fresh feed reading is written HERE first and
-- carries this row's id (valuation_store = 'xavier_probability_snapshots').
--
-- Append-only. No threshold, limit, cap, policy, live / SMALL LIVE switch or
-- funded control is touched; the 30 s probability rule is unchanged.

CREATE TABLE IF NOT EXISTS xavier_probability_snapshots (
    snapshot_id          bigserial   PRIMARY KEY,
    account_id           text        NOT NULL,
    group_id             text        NOT NULL,
    us_market_slug       text        NOT NULL,
    holding_side         text        NOT NULL,
    source               text        NOT NULL,
    probability          double precision NOT NULL,
    source_at            timestamptz,
    received_at          timestamptz,
    limit_s              double precision,
    payout_event         text,
    payout_is_complement boolean,
    evidence             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    recorded_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT xavier_probability_snapshots_paper_ck CHECK (
        account_id LIKE 'paper%'),
    CONSTRAINT xavier_probability_snapshots_side_ck CHECK (
        holding_side IN ('LONG', 'SHORT')),
    CONSTRAINT xavier_probability_snapshots_p_ck CHECK (
        probability >= 0 AND probability <= 1)
);
CREATE INDEX IF NOT EXISTS xavier_probability_snapshots_pos_idx
    ON xavier_probability_snapshots (group_id, us_market_slug, holding_side,
                                     snapshot_id DESC);

CREATE OR REPLACE FUNCTION xavier_probability_snapshots_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'xavier_probability_snapshots is append-only (%)', TG_OP;
END $$;

DROP TRIGGER IF EXISTS xavier_probability_snapshots_append_only_trg
    ON xavier_probability_snapshots;
CREATE TRIGGER xavier_probability_snapshots_append_only_trg
    BEFORE UPDATE OR DELETE ON xavier_probability_snapshots
    FOR EACH ROW EXECUTE FUNCTION xavier_probability_snapshots_append_only();
DROP TRIGGER IF EXISTS xavier_probability_snapshots_no_truncate_trg
    ON xavier_probability_snapshots;
CREATE TRIGGER xavier_probability_snapshots_no_truncate_trg
    BEFORE TRUNCATE ON xavier_probability_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION
    xavier_probability_snapshots_append_only();
