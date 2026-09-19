\echo ''
-- THE CLOSING CONTRACT, READ FROM PRODUCTION RATHER THAN INFERRED.
--
-- Owner directive 2026-09-19: BETTOR remains primary and its runtime
-- state is reported independently of RN1's. Every line below is a real
-- row's own value; a component with no row reads NOT_IDENTIFIED rather
-- than being omitted or assumed healthy.
--
-- Read-only: this file contains SELECTs and nothing else, and runs
-- under default_transaction_read_only like every research query.

\echo '--- RN1_BENCHMARK_RUNTIME_STATUS ---'
SELECT 'heartbeat|' || h.service || '|' || h.status || '|'
       || h.beat_at::text || '|'
       || round(extract(epoch FROM (now() - h.beat_at)))::text || 's|'
       || COALESCE(h.detail::text, 'NO_DETAIL')
  FROM service_heartbeats h
 WHERE h.service IN ('chain_listener', 'poller', 'shadow_rn1',
                     'shadow_bettor')
 ORDER BY h.service;

\echo '--- RN1_FEED_LAST_SOURCE_EVENT (the venue''s own stamp) ---'
SELECT 'rn1_feed_last_source_event|'
       || COALESCE(max(t.ts)::text, 'NOT_IDENTIFIED') || '|'
       || COALESCE(round(extract(epoch FROM (now() - max(t.ts))))::text,
                   'NOT_IDENTIFIED')
  FROM trades t
  JOIN whales w ON w.id = t.whale_id
 WHERE w.active IS TRUE;

\echo '--- RN1_FEED_LAST_RECEIVED_EVENT (when WE saw it) ---'
SELECT 'rn1_feed_last_received_event|'
       || COALESCE(max(t.detected_at)::text, 'NOT_IDENTIFIED') || '|'
       || COALESCE(round(extract(epoch FROM (now() - max(t.detected_at))))::text,
                   'NOT_IDENTIFIED')
  FROM trades t
  JOIN whales w ON w.id = t.whale_id
 WHERE w.active IS TRUE;

\echo '--- BETTOR_OPPORTUNITIES_OBSERVED ---'
-- to_regclass, because migration 071 may not have reached this database
-- yet and "the table is absent" is a different answer from "zero rows".
SELECT CASE WHEN to_regclass('public.bettor_opportunities') IS NULL
            THEN 'bettor_opportunities|TABLE_ABSENT'
            ELSE 'bettor_opportunities|PRESENT' END;

SELECT 'bettor_opportunities_observed|' || count(*)::text || '|'
       || COALESCE(max(observed_at)::text, 'NONE') || '|'
       || COALESCE(count(*) FILTER (
              WHERE observed_at > now() - interval '1 hour')::text, '0')
  FROM bettor_opportunities
 WHERE to_regclass('public.bettor_opportunities') IS NOT NULL;

\echo '--- SHADOW DECISIONS BY LANE AND ACTION ---'
SELECT 'shadow_decisions|' || d.lane || '|' || d.proposed_action || '|'
       || count(*)::text || '|'
       || COALESCE(max(d.decided_at)::text, 'NONE')
  FROM shadow_decisions d
 GROUP BY d.lane, d.proposed_action
 ORDER BY d.lane, d.proposed_action;

\echo '--- RN1 PROSPECTIVE OBSERVATIONS BY KIND ---'
SELECT 'rn1_observations|' || o.record_kind || '|' || count(*)::text || '|'
       || COALESCE(max(o.bettor_received_ts)::text, 'NONE')
  FROM rn1_observations o
 GROUP BY o.record_kind
 ORDER BY o.record_kind;

\echo '--- MIRROR SAFETY: read from the worker's own heartbeat ---'
-- There is no settings row for this: mirror_live is an environment
-- switch, and the only honest in-database witness is the mode the
-- worker itself reports. A missing heartbeat reads NOT_IDENTIFIED
-- rather than being taken for "off".
SELECT 'mirror_state|' || COALESCE(
           (SELECT h.status || '|' || h.beat_at::text || '|'
                   || COALESCE(h.detail::text, 'NO_DETAIL')
              FROM service_heartbeats h WHERE h.service = 'mirror_live'),
           'NOT_IDENTIFIED');

\echo '--- MIGRATIONS PRESENT ---'
SELECT 'migration|' || m.version
  FROM schema_migrations m
 WHERE m.version LIKE '07%'
 ORDER BY m.version;
