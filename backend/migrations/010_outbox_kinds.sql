-- 010: notification_outbox accepts every kind the pipeline can enqueue.
--
-- THE OUTAGE THIS FIXES (2026-07-27 20:44Z -> 2026-08-02). 001_init
-- constrained kind to ('webpush','telegram'); the pipeline later learned
-- 'sms' and 'ntfy', gated on env. The moment those env vars were set on
-- the deployed service, EVERY whale-trade ingest hit this check
-- constraint and the whole ingestion pipeline flatlined for six days —
-- discovered only when the copy sleeve needed detections. The constraint
-- must always be a superset of ingestion/pipeline.py's kinds list.

ALTER TABLE notification_outbox DROP CONSTRAINT IF EXISTS notification_outbox_kind_check;
ALTER TABLE notification_outbox
    ADD CONSTRAINT notification_outbox_kind_check
    CHECK (kind IN ('webpush', 'telegram', 'sms', 'ntfy'));
