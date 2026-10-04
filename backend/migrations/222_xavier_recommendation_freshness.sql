-- ══════════════════════════════════════════════════════════════════════
-- 222 · XAVIER'S RECOMMENDATION CARRIES ITS VALUATION AND ITS STATE
-- ══════════════════════════════════════════════════════════════════════
--
-- THE DEFECT (owner P0, 2026-10-04). A paper position's PinnAPI probability
-- went stale, EXIT / REDUCE were blocked for staleness, and Xavier's
-- assessment still recorded -- and every surface displayed -- HOLD as the
-- current recommendation: HOLD survived by default, not on fresh evidence.
--
-- WHAT THIS ADDS to xavier_management_assessments (migration 206), two
-- NULLABLE columns -- metadata only, no rewrite, no backfill, history
-- untouched (the table's immutability trigger still forbids UPDATE /
-- DELETE):
--   valuation             the valuation the recommendation stands on:
--                         source, source_at, observed_at, age, the freshness
--                         limit it was judged against (the EXISTING limit:
--                         paper session entry.pinnacle_max_age_s = the odds
--                         source's 30 s rule), expires_at, valuation id and
--                         hash (sportsassets.xavier_freshness.valuation_block)
--   recommendation_state  the state at the instant of writing: CURRENT,
--                         WAITING_FOR_FRESH_EVIDENCE,
--                         MANAGEMENT_UNAVAILABLE_STALE_INPUT or
--                         NO_RECOMMENDATION. STALE / INVALID are READ-TIME
--                         states (now - source_at > limit, a newer valuation,
--                         a mark move, a game-state change, a newer
--                         assessment) and are never stored.
--
-- (Every constraint below is added NOT VALID: the two new columns are NULL
-- on every existing row, and no history is re-checked under a table lock.)
--
-- AND ONE CHECK, NOT VALID (it binds every NEW row; historical rows that
-- recorded HOLD on stale evidence are left exactly as written and the read
-- layer shows them as STALE): a non-fresh assessment records no management
-- action at all -- only WAITING_FOR_FRESH_EVIDENCE /
-- MANAGEMENT_UNAVAILABLE_STALE_INPUT (or null). The existing 206 CHECK (no
-- EXIT / REDUCE / REALLOCATE on stale evidence) is unchanged.
--
-- AND THE REQUEUE TRIGGERS on paper_xavier_reviews (migration 172's trigger
-- CHECK is widened, never narrowed): ORDER_EVENT (a management order of the
-- group reached a terminal state -- protection changed / cancelled),
-- VALUATION_CHANGE (a newer stored valuation of the contract),
-- GAME_STATE_CHANGE (the event started after the last review) and
-- FRESHNESS_EXPIRY (the last review's fresh probability passed its source
-- stamp + the existing limit). The widened CHECK is added NOT VALID first
-- (no table scan under lock; every existing row satisfies the narrower
-- original) and the original is dropped in the same transaction.
--
-- NO CAPITAL AUTHORITY: records only; nothing here places, cancels or sizes
-- an order, and no threshold is defined or changed.

ALTER TABLE xavier_management_assessments
    ADD COLUMN IF NOT EXISTS valuation jsonb,
    ADD COLUMN IF NOT EXISTS recommendation_state text;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'xavier_assessments_rec_state_ck') THEN
        ALTER TABLE xavier_management_assessments
            ADD CONSTRAINT xavier_assessments_rec_state_ck CHECK (
                recommendation_state IS NULL
                OR (recommendation_state IN (
                        'CURRENT', 'WAITING_FOR_FRESH_EVIDENCE',
                        'MANAGEMENT_UNAVAILABLE_STALE_INPUT',
                        'NO_RECOMMENDATION')
                    AND (recommendation_state <> 'CURRENT'
                         OR evidence_state = 'FRESH_CURRENT_PROBABILITY')))
            NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'xavier_assessments_valuation_ck') THEN
        ALTER TABLE xavier_management_assessments
            ADD CONSTRAINT xavier_assessments_valuation_ck CHECK (
                valuation IS NULL OR jsonb_typeof(valuation) = 'object')
            NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'xavier_assessments_no_stale_action_ck')
    THEN
        ALTER TABLE xavier_management_assessments
            ADD CONSTRAINT xavier_assessments_no_stale_action_ck CHECK (
                evidence_state = 'FRESH_CURRENT_PROBABILITY'
                OR recommendation IS NULL
                OR recommendation IN ('WAITING_FOR_FRESH_EVIDENCE',
                                      'MANAGEMENT_UNAVAILABLE_STALE_INPUT'))
            NOT VALID;
    END IF;
    IF to_regclass('paper_xavier_reviews') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conname = 'paper_xavier_reviews_trigger_v2_ck')
    THEN
        ALTER TABLE paper_xavier_reviews
            ADD CONSTRAINT paper_xavier_reviews_trigger_v2_ck CHECK (
                trigger IN ('FIRST_FILL', 'FILL_EVENT', 'MARKET_EVENT',
                            'SCHEDULED_BACKSTOP', 'ORDER_EVENT',
                            'VALUATION_CHANGE', 'GAME_STATE_CHANGE',
                            'FRESHNESS_EXPIRY')) NOT VALID;
        ALTER TABLE paper_xavier_reviews
            DROP CONSTRAINT IF EXISTS paper_xavier_reviews_trigger_ck;
    END IF;
END $$;
