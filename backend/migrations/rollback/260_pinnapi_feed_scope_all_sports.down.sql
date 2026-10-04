-- Rollback of 260: puts back the scope row 260 replaced (kept under
-- "replaced"), or removes 260's row when it replaced none. Only a row that is
-- still the one 260 wrote is touched; an operator's later scope is left as
-- it is. The owner reads the row at its next start.
UPDATE ingestion_state
   SET value = value->'replaced'
 WHERE key = 'pinnapi_feed_scope'
   AND value->>'why' = 'migration 260: every sport at both Pinnacle and the venue'
   AND jsonb_typeof(value->'replaced') = 'object';
DELETE FROM ingestion_state
 WHERE key = 'pinnapi_feed_scope'
   AND value->>'why' = 'migration 260: every sport at both Pinnacle and the venue';
