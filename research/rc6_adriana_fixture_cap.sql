-- RC6 lane Adriana (category 11, SHADOW), read only. The claim-first scan
-- reads at most 80 ESTABLISHED Kalshi fixtures (canonical_claims_db.
-- MAX_FIXTURES) in (-4 h, +36 h), ordered by start time, BEFORE it drops
-- fixtures without a readable Kalshi book. This measures what that cap cuts
-- at one instant, under the deployed order and under a cross-venue-first,
-- readable-first order, and how long Adriana's passes take (the claim scan
-- runs inside a 45 s phase timeout in the API process).
\echo === A. the window: ESTABLISHED fixtures, readable, mapped to PMUS ===
WITH fx AS (
  SELECT f.event_ticker, f.start_at, f.pmus_mapping_status = 'ESTABLISHED' AS pm,
         EXISTS (SELECT 1 FROM kalshi_books_current b
                  WHERE b.readable AND (b.ticker = ANY(f.team_tickers)
                                        OR b.ticker = f.tie_ticker)) AS rd
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours')
SELECT count(*) AS in_window, count(*) FILTER (WHERE pm) AS pmus_mapped,
       count(*) FILTER (WHERE rd) AS readable,
       count(*) FILTER (WHERE rd AND pm) AS readable_pmus_mapped
  FROM fx;
\echo === B. what LIMIT 80 keeps and cuts: deployed order vs cross-venue-first ===
WITH fx AS (
  SELECT f.event_ticker, f.start_at, f.pmus_mapping_status = 'ESTABLISHED' AS pm,
         EXISTS (SELECT 1 FROM kalshi_books_current b
                  WHERE b.readable AND (b.ticker = ANY(f.team_tickers)
                                        OR b.ticker = f.tie_ticker)) AS rd,
         coalesce(array_length(f.team_tickers, 1), 0)
           + CASE WHEN f.tie_ticker IS NULL THEN 0 ELSE 1 END AS kt
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'),
o AS (
  SELECT fx.*,
         row_number() OVER (ORDER BY start_at, event_ticker) AS r_deployed,
         row_number() OVER (ORDER BY pm DESC, rd DESC, start_at, event_ticker) AS r_new
    FROM fx)
SELECT ord, kept,
       count(*) AS fixtures,
       count(*) FILTER (WHERE rd) AS readable,
       count(*) FILTER (WHERE rd AND pm) AS readable_pmus_mapped,
       sum(2 * kt) FILTER (WHERE rd) AS kalshi_aliases_of_readable,
       2 * count(*) FILTER (WHERE rd AND pm) AS pmus_aliases_of_readable
  FROM (SELECT 'deployed: start_at' AS ord, r_deployed <= 80 AS kept, * FROM o
        UNION ALL
        SELECT 'new: pmus, readable, start_at', r_new <= 80, * FROM o) x
 GROUP BY 1, 2 ORDER BY 1, 2 DESC;
\echo === C. Adriana pass durations and phase errors, last 24 h ===
SELECT count(*) AS runs,
       count(*) FILTER (WHERE finished_at IS NULL) AS unfinished,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY (summary->>'elapsed_s')::numeric)::numeric, 2) AS p50_elapsed_s,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY (summary->>'elapsed_s')::numeric)::numeric, 2) AS p95_elapsed_s,
       max((summary->>'elapsed_s')::numeric) AS max_elapsed_s,
       count(*) FILTER (WHERE summary->'phase_errors' ? 'claims') AS claims_phase_errors,
       count(*) FILTER (WHERE summary->'phase_errors' <> '{}'::jsonb) AS any_phase_error
  FROM agent_runs
 WHERE agent_id = 'ADRIANA' AND started_at > now() - interval '24 hours';
\echo === D. the phase errors by kind, last 24 h ===
SELECT e.key AS phase, e.value AS error, count(*) AS runs
  FROM agent_runs, jsonb_each_text(summary->'phase_errors') e
 WHERE agent_id = 'ADRIANA' AND started_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
