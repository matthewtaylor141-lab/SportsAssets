-- Rollback of 193: removes the scope row only if it is still the one 193
-- wrote (the owner then falls back to DEFAULT_SCOPE [6] at its next start).
DELETE FROM ingestion_state WHERE key = 'pinnapi_feed_scope'
   AND value->>'why' = 'migration 193: soccer added for held paper positions';
