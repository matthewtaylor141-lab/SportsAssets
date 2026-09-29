-- down for 143. The five columns are a record only -- nothing gates on them --
-- so they may be dropped; the values go with them and the writer returns to
-- the 137 column set.
ALTER TABLE ext_candidate_outcomes
    DROP CONSTRAINT IF EXISTS ext_candidate_replaced_is_native_ck,
    DROP CONSTRAINT IF EXISTS ext_candidate_mapped_by_ck,
    DROP COLUMN IF EXISTS global_refusal_replaced,
    DROP COLUMN IF EXISTS mapped_by,
    DROP COLUMN IF EXISTS quote_age_s,
    DROP COLUMN IF EXISTS our_processing_s,
    DROP COLUMN IF EXISTS provider_lag_s;
