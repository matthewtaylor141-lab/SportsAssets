-- down for 368. The five columns are a record only -- nothing gates on them --
-- so they may be dropped; the values go with them and the writer returns to
-- the 143 column set (it checks for the columns before every write).
ALTER TABLE ext_candidate_outcomes
    DROP COLUMN IF EXISTS book_source,
    DROP COLUMN IF EXISTS http_s,
    DROP COLUMN IF EXISTS cooldown_wait_s,
    DROP COLUMN IF EXISTS gate_wait_s,
    DROP COLUMN IF EXISTS queue_wait_s;
