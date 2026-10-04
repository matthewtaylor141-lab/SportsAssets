-- Rolls back 213. Evidence only: nothing decides from these tables or
-- columns. Without them the workers fall back to the 210 probe columns, the
-- C12 proof write is skipped, and GET /api/command/p5/evidence reports the
-- focus universe and the C12 proofs as null with the reason (fail closed).
DROP TABLE IF EXISTS p5_c12_decision_proof;
DROP TABLE IF EXISTS institutional_focus_universe;
DROP INDEX IF EXISTS institutional_same_book_probe_tier_idx;
ALTER TABLE institutional_same_book_probe
    DROP CONSTRAINT IF EXISTS institutional_same_book_probe_agreed_ck,
    DROP CONSTRAINT IF EXISTS institutional_same_book_probe_nc_reason_ck,
    DROP CONSTRAINT IF EXISTS institutional_same_book_probe_exact_ck,
    DROP COLUMN IF EXISTS institutional_symbol,
    DROP COLUMN IF EXISTS identity_exact,
    DROP COLUMN IF EXISTS outcome_side,
    DROP COLUMN IF EXISTS bettor_event,
    DROP COLUMN IF EXISTS retail_event_slug,
    DROP COLUMN IF EXISTS inst_best_bid,
    DROP COLUMN IF EXISTS inst_best_ask,
    DROP COLUMN IF EXISTS retail_best_bid,
    DROP COLUMN IF EXISTS retail_best_ask,
    DROP COLUMN IF EXISTS gap_state,
    DROP COLUMN IF EXISTS inst_receipt_age_s,
    DROP COLUMN IF EXISTS retail_receipt_age_s,
    DROP COLUMN IF EXISTS compared_at,
    DROP COLUMN IF EXISTS agreed,
    DROP COLUMN IF EXISTS incomparable_reason,
    DROP COLUMN IF EXISTS focus_tier,
    DROP COLUMN IF EXISTS focus_tier_rank,
    DROP COLUMN IF EXISTS focus_why,
    DROP COLUMN IF EXISTS focus_universe_id;
