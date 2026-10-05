-- 260 · PINNAPI FEED SCOPE: EVERY SPORT AT BOTH PINNACLE AND THE VENUE
--
-- THE DEFECT (R30A P0 incident "coverage -> trade starvation", root cause
-- RC1, production read-only 2026-10-04). The PinnAPI WebSocket subscribed
-- sport ids [6, 1] only -- the row migration 193 wrote -- so football,
-- hockey, basketball and tennis had no Pinnacle WebSocket price at all: 616
-- venue events per 24 h (77% of the real-sport winner universe) and 29,446
-- contracts. NFL and NCAAF fell back to the metered provider, whose quotes
-- were stale on arrival in most cycles.
--
-- THE SCOPE. PinnAPI's own sport ids ("## Sport IDs -- Stable integer
-- mapping": 1 Soccer, 2 Tennis, 3 Basketball, 4 Hockey, 5 Football,
-- 6 Baseball; tests/fixtures/pinnapi_ws_subscription_docs_2026_10_04.json),
-- the six sports the owner named, each listed at both Pinnacle (PinnAPI
-- probe run 37232918224) and the venue (research-sql run 37235155757, L1).
-- Live and prematch, as before.
--
-- INSIDE THE PROVIDER'S DOCUMENTED LIMITS: one socket per account (the
-- owner's existing socket; nothing new is opened), sport-level
-- subscriptions carry no documented cap (only event-id subscriptions are
-- capped, at 200 per stream). The cache bounds are unchanged and the
-- arithmetic is in pinnapi_feed ("CAPACITY FOR THE R30A SCOPE"): 45,136
-- markets worst case against MAX_MARKETS 120,000. pinnapi_feed_runtime.
-- scope_of admits exactly these six and names any other id it refuses.
--
-- DO UPDATE, NOT DO NOTHING. 193 inserted only when no row existed, so its
-- row is what production holds and DO NOTHING would change nothing. The row
-- it replaces is kept inside the new value under "replaced" (the rollback
-- restores it), and a re-run changes nothing (the WHERE compares the "why").
-- The owner reads the row at process start, so this applies from the next
-- start -- the deploy that carries this migration.
--
-- Data input only: no order, threshold, freshness rule, settlement rule or
-- control is read from here. Nothing is subscribed until the feed is armed
-- ('pinnapi_feed' = true), exactly as before.
INSERT INTO ingestion_state (key, value)
VALUES ('pinnapi_feed_scope',
        '{"sport_ids": [1, 2, 3, 4, 5, 6], "streams": ["live", "prematch"],
          "why": "migration 260: every sport at both Pinnacle and the venue"}'::jsonb)
ON CONFLICT (key) DO UPDATE
   SET value = EXCLUDED.value
               || jsonb_build_object('replaced', ingestion_state.value)
 WHERE ingestion_state.value->>'why'
       IS DISTINCT FROM EXCLUDED.value->>'why';
