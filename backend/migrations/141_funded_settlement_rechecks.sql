-- ══════════════════════════════════════════════════════════════════════
-- 141 · FUNDED SETTLEMENT RE-READS: WHAT THE VENUE SAYS NOW ABOUT A LEG
--       WE ALREADY CLOSED
-- ══════════════════════════════════════════════════════════════════════
--
-- THE GAP. A funded leg was closed once, on the venue's settlement as read
-- at the time (`reconcile_settlement`), and never read again. A venue that
-- later CORRECTS that settlement left the booked payout, and the pairing
-- model's label derived from it, standing on a reading the venue no longer
-- gives. An approved model trained on that label could never be invalidated
-- by a correction, because nothing would ever see one.
--
-- WHAT THIS RECORDS. Each re-read of a closed funded leg, through the same
-- probe and the same authoritative-reading bar the close used, and a verdict:
--
--   AGREES           the venue's reading now is the one we booked
--   DISAGREES        the venue now states a different authoritative reading
--   NOT_ESTABLISHED  the re-read was not authoritative (a failure, an
--                    unreadable or contradicted endpoint): it says nothing
--
-- WHAT A DISAGREEMENT DOES, and what it does NOT do:
--   * the leg's group stops being a label (`bettor_funded_model.LABEL_SQL`),
--     so any model whose provenance names it no longer reproduces: `approved`
--     refuses to price on it and the scheduled pass retires it. Servicing --
--     HOLD, exits, reductions -- does not depend on a model and continues;
--   * the account is refused NEW exposure while the leg's newest established
--     re-read disagrees (`bettor_funded_activation.account_selection`);
--     exits report account state and are never gated on it;
--   * the BOOKED ACCOUNTING IS NOT REWRITTEN. The economics rows are the
--     record of what was booked when; correcting them is an accounting
--     decision with its own evidence, and a re-read is not that decision.
--
-- APPEND-ONLY. ADDITIVE.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_funded_settlement_rechecks (
    recheck_id          bigserial   PRIMARY KEY,
    -- NOT A FOREIGN KEY, deliberately: a re-read is a record of what the
    -- venue said about that id at that instant, and an intent row is never
    -- deleted in production, so the key adds no integrity there and would
    -- make this append-only record block every removal of an intent.
    intent_id           text        NOT NULL,
    read_at             timestamptz NOT NULL,
    booked_reading      text,
    booked_payout_price numeric,
    venue_reading       text,
    venue_payout_price  numeric,
    verdict             text        NOT NULL,
    why                 text,
    probe               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT bettor_funded_recheck_verdict_ck CHECK (
        verdict IN ('AGREES', 'DISAGREES', 'NOT_ESTABLISHED')),
    -- A DISAGREEMENT NAMES BOTH SIDES OF IT: what was booked, and the
    -- authoritative reading the venue gives now.
    CONSTRAINT bettor_funded_recheck_disagreement_ck CHECK (
        verdict <> 'DISAGREES'
        OR (venue_reading IS NOT NULL AND booked_reading IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS bettor_funded_rechecks_intent_idx
    ON bettor_funded_settlement_rechecks (intent_id, read_at DESC);
CREATE INDEX IF NOT EXISTS bettor_funded_rechecks_disagree_idx
    ON bettor_funded_settlement_rechecks (intent_id)
    WHERE verdict = 'DISAGREES';

CREATE OR REPLACE FUNCTION bettor_funded_recheck_is_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'settlement re-read % of % is a record; it is not edited',
        OLD.recheck_id, OLD.intent_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_funded_recheck_is_append_only_trg
    ON bettor_funded_settlement_rechecks;
CREATE TRIGGER bettor_funded_recheck_is_append_only_trg
    BEFORE UPDATE OR DELETE ON bettor_funded_settlement_rechecks
    FOR EACH ROW EXECUTE FUNCTION bettor_funded_recheck_is_append_only();

COMMIT;
