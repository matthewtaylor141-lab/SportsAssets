-- 366 rollback: the ACTUAL lane's aggregate exposure cap and Audrey's STALE.
--
-- Both objects 366 added are removed, and nothing else was changed by 366,
-- so nothing else is restored:
--   * execmirror_exposure_caps is dropped. A release without it reads no
--     aggregate cap (the code before 366 had none; this release's code reads
--     a missing table as no row -> max_order_usd, fail closed). A cap an
--     owner configured is lost with the table: record it before rolling back
--     if it is to be set again.
--   * smalllive_reconciliations.stale_reason is dropped. The groups Audrey
--     recorded as STALE (stored PENDING + the reason) remain as PENDING rows,
--     the one existing "not final" status; the previous release rewrites
--     each group's status on its next pass. The status CHECK was never
--     touched, so there is no constraint to put back.
-- Apply it together with (or after) rolling the CODE back to the previous
-- release: the current release's Audrey and readers name stale_reason, so
-- they must not run on the schema this leaves. (The cap reader alone
-- tolerates a missing table, as above.)
-- Safe to apply twice; 366 can be applied again afterwards.
DROP TABLE IF EXISTS execmirror_exposure_caps;
ALTER TABLE smalllive_reconciliations
    DROP COLUMN IF EXISTS stale_reason;
