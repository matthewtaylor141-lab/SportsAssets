-- ECONOMIC FUNNEL (owner directive section 6), STEP 5b: WHERE THE
-- STRUCTURAL-ARBITRAGE AND ROUTING MECHANISMS STOP, BY CODE.
--
-- READ-ONLY. Every statement is a SELECT. Run through research-sql.yml.
-- ef_mechanisms.sql run 37790435499 found 828 OK Adriana scans with 5,566
-- structures considered and 0 opportunities, and 8,828 canonical route
-- receipts with no runner-up route. This says why, from the scans' own
-- refusal counters and the receipts' own candidate lists.

\echo '== 5b.1 Adriana: refusal codes summed over every scan (by_code)'
SELECT k.key AS code, sum((k.value)::text::numeric) AS refusals, count(*) AS scans
  FROM adriana_arb_scans s
  CROSS JOIN LATERAL jsonb_each(
       CASE WHEN jsonb_typeof(s.by_code) = 'object' THEN s.by_code ELSE '{}'::jsonb END) AS k(key, value)
 WHERE jsonb_typeof(k.value) = 'number'
 GROUP BY 1 ORDER BY refusals DESC LIMIT 40;

\echo '== 5b.2 Adriana: verdicts and structure kinds summed over every scan'
SELECT 'by_verdict' AS dim, k.key, sum((k.value)::text::numeric) AS n
  FROM adriana_arb_scans s
  CROSS JOIN LATERAL jsonb_each(
       CASE WHEN jsonb_typeof(s.by_verdict) = 'object' THEN s.by_verdict ELSE '{}'::jsonb END) AS k(key, value)
 WHERE jsonb_typeof(k.value) = 'number'
 GROUP BY 1, 2
UNION ALL
SELECT 'by_kind', k.key, sum((k.value)::text::numeric)
  FROM adriana_arb_scans s
  CROSS JOIN LATERAL jsonb_each(
       CASE WHEN jsonb_typeof(s.by_kind) = 'object' THEN s.by_kind ELSE '{}'::jsonb END) AS k(key, value)
 WHERE jsonb_typeof(k.value) = 'number'
 GROUP BY 1, 2
 ORDER BY 1, 3 DESC;

\echo '== 5b.3 Adriana: the newest OK scan in full (venues, limits, authority)'
SELECT s.scan_id, s.started_at, s.markets_read, s.books_fresh, s.structures_considered,
       s.opportunities, s.refusals_total, left(s.venues::text, 400) AS venues,
       left(s.by_code::text, 600) AS by_code, left(s.authority::text, 300) AS authority
  FROM adriana_arb_scans s WHERE s.status = 'OK'
 ORDER BY s.started_at DESC LIMIT 1;

\echo '== 5b.4 routing: chosen venue, candidate count and losers per receipt'
SELECT coalesce(r.best_single ->> 'venue', '(none)') AS best_venue,
       coalesce(r.chosen ->> 'venue', '(none)') AS chosen_venue,
       CASE WHEN jsonb_typeof(r.candidates) = 'array' THEN jsonb_array_length(r.candidates) END AS candidates,
       CASE WHEN jsonb_typeof(r.lost) = 'array' THEN jsonb_array_length(r.lost) END AS lost,
       coalesce(r.refusal, '(chosen)') AS refusal,
       count(*) AS receipts, count(DISTINCT r.event_key) AS events
  FROM canonical_route_receipts r
 GROUP BY 1, 2, 3, 4, 5 ORDER BY receipts DESC LIMIT 30;

\echo '== 5b.5 routing: why candidates lost (first reason per lost candidate)'
SELECT coalesce(l.value ->> 'venue', '?') AS venue,
       coalesce(l.value ->> 'reason', l.value ->> 'refusal', l.value ->> 'why', '?') AS reason,
       count(*) AS candidates
  FROM canonical_route_receipts r
  CROSS JOIN LATERAL jsonb_array_elements(
       CASE WHEN jsonb_typeof(r.lost) = 'array' THEN r.lost ELSE '[]'::jsonb END) AS l(value)
 GROUP BY 1, 2 ORDER BY candidates DESC LIMIT 30;
