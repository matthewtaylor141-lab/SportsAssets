-- RC6.2 lane p-coverage (DIAGNOSE), read only. The LIVE active Kalshi
-- registry rows per series (the venue's own series ticker), so the RC6.1
-- Kalshi ontology's per-series outcome -- measured by running
-- kalshi_ontology.classify on the 80,659-row real corpus of 2026-10-09
-- ~01:30Z (research rc6_kalshi_ontology_corpus_0..3) -- can be applied to
-- today's population series by series instead of by one global ratio.
-- No write, no secret.
\echo === L1. active Kalshi rows per series: [series, n] x 60 per line ===
WITH k AS (SELECT coalesce(competition, '<null>') AS series, count(*) AS n
             FROM market_plane_registry
            WHERE active AND venue = 'KALSHI' GROUP BY 1),
b AS (SELECT k.*, (row_number() OVER (ORDER BY series) - 1) / 60 AS grp
        FROM k)
SELECT json_agg(json_build_array(series, n) ORDER BY series)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
\echo === L2. totals ===
SELECT count(*) AS active_kalshi, count(DISTINCT competition) AS series,
       now() AS read_at
  FROM market_plane_registry WHERE active AND venue = 'KALSHI';
