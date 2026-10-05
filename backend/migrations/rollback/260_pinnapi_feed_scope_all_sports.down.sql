-- Rollback of 260: puts back the scope row 260 replaced (kept under
-- "replaced"), or removes 260's row when it replaced none. Only a row that is
-- still EXACTLY the one 260 wrote is touched -- its "why", its sport ids and
-- its streams as 260 wrote them -- so an operator's later scope is left as it
-- is, including an edit that kept the "why" (adversarial verification, fix
-- stage 2026-10-05: a `jsonb_set` of sport_ids to [1, 5, 6] was overwritten
-- with 193's row, because the row was identified by its "why" alone). The
-- owner reads the row at its next start.
UPDATE ingestion_state
   SET value = value->'replaced'
 WHERE key = 'pinnapi_feed_scope'
   AND value->>'why' = 'migration 260: every sport at both Pinnacle and the venue'
   AND value->'sport_ids' = '[1, 2, 3, 4, 5, 6]'::jsonb
   AND value->'streams' = '["live", "prematch"]'::jsonb
   AND jsonb_typeof(value->'replaced') = 'object';
DELETE FROM ingestion_state
 WHERE key = 'pinnapi_feed_scope'
   AND value->>'why' = 'migration 260: every sport at both Pinnacle and the venue'
   AND value->'sport_ids' = '[1, 2, 3, 4, 5, 6]'::jsonb
   AND value->'streams' = '["live", "prematch"]'::jsonb;
