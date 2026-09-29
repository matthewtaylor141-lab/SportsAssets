-- ══════════════════════════════════════════════════════════════════════
-- 143 · EVERY CANDIDATE ROW SAYS HOW OLD ITS PRICE WAS ON ARRIVAL, AND
--       WHICH CATALOGUE ITS VENUE CONTRACT CAME FROM
-- ══════════════════════════════════════════════════════════════════════
--
-- THE GAP, PART ONE: STALE ON ARRIVAL. `ext_candidate_outcomes` (137) holds one
-- row per provider event per cycle and said nothing about the age of the
-- price. The cycle's latency figures sampled EVALUATED candidates only, so an
-- event refused QUOTE_STALE_ON_ARRIVAL -- or refused before mapping -- left no
-- lag at all, and "the provider was late" could not be told from "we were
-- slow" for exactly the events where the answer matters.
--
--   provider_lag_s    received_at - the provider's own last_update
--   our_processing_s  our arrival instant - received_at
--   quote_age_s       our arrival instant - the provider's last_update
--
-- "Arrival" is the instant the cycle examined that event's price: the lever-A
-- check before the venue read where the event got that far, otherwise the
-- moment its Pinnacle quote was read. NULL means a clock was not available,
-- never zero.
--
-- THE GAP, PART TWO: WHICH CATALOGUE MAPPED IT. The venue-native identity path
-- (`bettor_venue_native_identity`) maps an event through the US venue's own
-- catalogue when the global one has no row for it. A row that does not say
-- which path produced its contract cannot be audited, so:
--
--   mapped_by                'GLOBAL_CATALOGUE' or 'VENUE_NATIVE'
--   global_refusal_replaced  the global refusal the venue-native path
--                            replaced (e.g. NO_VENUE_CONTRACT_FOR_EVENT)
--
-- ADDITIVE AND NULLABLE. No existing row or column changes, and the writer
-- falls back to the 137 column set -- reporting that it did -- on a database
-- where this has not been applied yet.

BEGIN;

ALTER TABLE ext_candidate_outcomes
    ADD COLUMN IF NOT EXISTS provider_lag_s          double precision,
    ADD COLUMN IF NOT EXISTS our_processing_s        double precision,
    ADD COLUMN IF NOT EXISTS quote_age_s             double precision,
    ADD COLUMN IF NOT EXISTS mapped_by               text,
    ADD COLUMN IF NOT EXISTS global_refusal_replaced text;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'ext_candidate_mapped_by_ck') THEN
        ALTER TABLE ext_candidate_outcomes
            ADD CONSTRAINT ext_candidate_mapped_by_ck CHECK (
                mapped_by IS NULL
                OR mapped_by IN ('GLOBAL_CATALOGUE', 'VENUE_NATIVE'));
    END IF;
    -- A replaced refusal is only ever recorded on a venue-native row.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'ext_candidate_replaced_is_native_ck') THEN
        ALTER TABLE ext_candidate_outcomes
            ADD CONSTRAINT ext_candidate_replaced_is_native_ck CHECK (
                global_refusal_replaced IS NULL
                OR mapped_by = 'VENUE_NATIVE');
    END IF;
END $$;

COMMIT;
