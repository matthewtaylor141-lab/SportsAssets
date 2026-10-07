-- READ-ONLY. mirror_shadow heartbeat detail; Xavier packet missing elements
-- across current reviews; canary inputs. Every statement is a SELECT.
SELECT service, status, beat_at, left(detail::text, 3000) AS detail
  FROM service_heartbeats
 WHERE service IN ('mirror_shadow', 'universal_market_plane')
 ORDER BY beat_at DESC LIMIT 3;

SELECT elem, count(*) AS n
  FROM (SELECT DISTINCT ON (group_id) group_id,
               selection->'management_packet' AS mp
          FROM paper_xavier_reviews
         WHERE reviewed_at > now() - interval '2 hours'
         ORDER BY group_id, reviewed_at DESC) r,
       LATERAL jsonb_array_elements_text(
           coalesce(r.mp->'gate'->'missing', '[]'::jsonb)) AS elem
 GROUP BY 1 ORDER BY 2 DESC LIMIT 30;

SELECT count(*) AS current_reviews,
       count(*) FILTER (WHERE (mp->'gate'->>'complete')::boolean) AS complete,
       left((array_agg(mp->'gate'))[1]::text, 1500) AS sample_gate
  FROM (SELECT DISTINCT ON (group_id) group_id,
               selection->'management_packet' AS mp
          FROM paper_xavier_reviews
         WHERE reviewed_at > now() - interval '2 hours'
         ORDER BY group_id, reviewed_at DESC) r;
