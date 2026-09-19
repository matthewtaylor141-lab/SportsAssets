-- LIVE SYMBOLS FOR THE INSTITUTIONAL L2 SHAPE VERIFICATION.
--
-- Owner directive 2026-09-19 21:5xZ §3: "Take a small read-only sample
-- on exact production symbols ... Do not adapt from guessed field
-- names."
--
-- The institutional `symbol` IS the retail slug (established 2026-09-10
-- and recorded in docs/mirror-coverage.md), so the markets BETTOR's
-- collector is reading right now are exactly the symbols to ask the
-- institutional book for. The first page of /v1/refdata/instruments
-- answers expired commodity rows, which cannot show a depth shape.
--
-- READABLE ONLY. A market whose book we could not read is not a useful
-- subject for verifying what a readable book looks like.

\echo '--- 1. THE MOST RECENTLY READABLE BETTOR SUBJECTS ---'
SELECT 'live|' || o.symbol
       || '|leg=' || coalesce(o.outcome_leg, 'NONE')
       || '|bid=' || coalesce((o.microstructure ->> 'bid'), 'NULL')
       || '|ask=' || coalesce((o.microstructure ->> 'ask'), 'NULL')
       || '|at=' || to_char(o.observed_at, 'HH24:MI:SSZ')
  FROM bettor_opportunities o
 WHERE o.observed_at > now() - interval '20 minutes'
   AND (o.microstructure ->> 'status') = 'MEASURED'
   AND (o.microstructure ->> 'bid') IS NOT NULL
   AND (o.microstructure ->> 'ask') IS NOT NULL
 ORDER BY o.observed_at DESC
 LIMIT 12;

\echo ''
\echo '--- 2. HOW MANY SUBJECTS ARE READABLE AT ALL RIGHT NOW ---'
SELECT 'readable|' || count(*) FILTER (
           WHERE (microstructure ->> 'status') = 'MEASURED')
       || '|unreadable|' || count(*) FILTER (
           WHERE (microstructure ->> 'status') <> 'MEASURED')
       || '|window=20min'
  FROM bettor_opportunities
 WHERE observed_at > now() - interval '20 minutes';

\echo ''
\echo '--- 3. DISTINCT SPORTS IN THE LIVE UNIVERSE ---'
SELECT 'universe|' || coalesce(split_part(symbol, '-', 2), 'NONE')
       || '|' || count(*)
  FROM bettor_opportunities
 WHERE observed_at > now() - interval '60 minutes'
 GROUP BY split_part(symbol, '-', 2)
 ORDER BY count(*) DESC
 LIMIT 10;
