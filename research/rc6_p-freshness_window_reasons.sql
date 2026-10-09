-- RC6.2 lane p-freshness, read only. The frozen window 2026-10-09 15:00Z
-- on the RC6.1 plane: every sample's named reasons summed (the X reasons
-- name their source -- REFRESH:MARKET_NOT_OPEN is the plane's own book read
-- inside the bound; PAPER_REST / STREAM name the others), and per member
-- the samples coded X and N, to size what holding a TERMINAL market out of
-- the re-reads changes in the window measure.
\echo === R1. reasons summed over the window's samples ===
SELECT r.key AS reason, sum(r.value::int) AS member_samples
  FROM market_plane_events e, jsonb_each_text(e.payload -> 'reasons') r
 WHERE e.kind = 'FRESHNESS_SAMPLE' AND (e.payload ->> 'window_start')::float8 = 1791558000
 GROUP BY 1 ORDER BY 2 DESC;
\echo === R2. per member: samples X and N (members with any X) ===
WITH w AS (
  SELECT payload FROM market_plane_events WHERE event_key = 'fwin:1791558000'),
m AS (
  SELECT (e.ord - 1)::int AS idx, e.r ->> 0 AS contract_id
    FROM w, jsonb_array_elements(w.payload -> 'members') WITH ORDINALITY AS e(r, ord)),
s AS (
  SELECT payload ->> 'codes' AS codes FROM market_plane_events
   WHERE kind = 'FRESHNESS_SAMPLE' AND (payload ->> 'window_start')::float8 = 1791558000)
SELECT m.contract_id,
       count(*) FILTER (WHERE substr(s.codes, m.idx + 1, 1) = 'X') AS x,
       count(*) FILTER (WHERE substr(s.codes, m.idx + 1, 1) = 'N') AS n,
       count(*) AS samples
  FROM m CROSS JOIN s
 GROUP BY 1 HAVING count(*) FILTER (WHERE substr(s.codes, m.idx + 1, 1) = 'X') > 0
 ORDER BY 2 DESC, 1;
