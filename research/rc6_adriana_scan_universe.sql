-- RC6 lane Adriana (category 11, SHADOW), read only. Why the recorded-books
-- census reads one market with no fresh book, what the cross-venue claim scan
-- has to work with (Kalshi fixtures mapped to PMUS, their recorded books on
-- both venues), and the venues' published void / postponement clauses as the
-- registry captured them.
\echo === A. adriana_arb_scans, last 6 h (newest 40) ===
SELECT scan_id, started_at, status, left(coalesce(why, ''), 60) AS why,
       markets_read, books_fresh, structures_considered, opportunities,
       refusals_total, left((by_code->'skipped')::text, 240) AS skipped
  FROM adriana_arb_scans WHERE started_at > now() - interval '6 hours'
 ORDER BY started_at DESC LIMIT 40;
\echo === A2. scans per day by kind (7 d) ===
SELECT date_trunc('day', started_at) AS d, split_part(scan_id, '-', 2) AS kind,
       count(*) AS n, max(markets_read) AS max_read, max(books_fresh) AS max_fresh,
       max(structures_considered) AS max_structures
  FROM adriana_arb_scans WHERE started_at > now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1 DESC, 2;
\echo === B. paper_book_observations, last 15 min, by first slug segment ===
SELECT split_part(us_market_slug, '-', 1) AS pfx, count(*) AS n_rows,
       count(DISTINCT us_market_slug) AS slugs,
       count(*) FILTER (WHERE error IS NULL) AS ok_rows,
       round(extract(epoch FROM now() - max(observed_at))::numeric, 1) AS newest_age_s
  FROM paper_book_observations WHERE observed_at > now() - interval '15 minutes'
 GROUP BY 1 ORDER BY 2 DESC;
\echo === B2. last 15 min tsc / asc slugs: newest age, sources ===
SELECT us_market_slug, count(*) AS n,
       round(extract(epoch FROM now() - max(observed_at))::numeric, 1) AS age_s,
       min(source) AS src_a, max(source) AS src_b,
       bool_or(error IS NOT NULL) AS any_err
  FROM paper_book_observations WHERE observed_at > now() - interval '15 minutes'
   AND (us_market_slug LIKE 'tsc-%' OR us_market_slug LIKE 'asc-%')
 GROUP BY 1 ORDER BY 3 LIMIT 80;
\echo === B3. inter-observation gap per slug, last 2 h, by first segment ===
WITH g AS (
  SELECT us_market_slug,
         observed_at - lag(observed_at) OVER (PARTITION BY us_market_slug
                                              ORDER BY observed_at) AS gap
    FROM paper_book_observations
   WHERE observed_at > now() - interval '2 hours'
     AND (us_market_slug LIKE 'tsc-%' OR us_market_slug LIKE 'asc-%'
          OR us_market_slug LIKE 'aec-%'))
SELECT split_part(us_market_slug, '-', 1) AS pfx, count(*) AS gaps,
       count(DISTINCT us_market_slug) AS slugs,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM gap)) AS p50_s,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM gap)) AS p90_s
  FROM g WHERE gap IS NOT NULL GROUP BY 1;
\echo === B4. writers of paper_book_observations, last 1 h ===
SELECT source, read_basis, count(*) AS n, count(DISTINCT us_market_slug) AS slugs
  FROM paper_book_observations WHERE observed_at > now() - interval '1 hour'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;
\echo === C. kalshi_fixtures_current in the claim window (-4 h, +36 h) ===
SELECT mapping_status, coalesce(pmus_mapping_status, '<null>') AS pm, league,
       outcome_kind, count(*) AS n
  FROM kalshi_fixtures_current
 WHERE start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 60;
\echo === C2. PMUS-mapped fixtures: recorded book ages on both venues ===
SELECT f.event_ticker, f.league, f.start_at, f.pmus_slug, f.outcome_kind,
       (SELECT round(extract(epoch FROM now() - max(o.observed_at))::numeric, 0)
          FROM paper_book_observations o WHERE o.us_market_slug = f.pmus_slug
           AND o.observed_at > now() - interval '2 days') AS pmus_book_age_s,
       (SELECT count(*) FROM paper_book_observations o
         WHERE o.us_market_slug = f.pmus_slug
           AND o.observed_at > now() - interval '15 minutes') AS pmus_rows_15m,
       (SELECT round(max(extract(epoch FROM now() - b.observed_at))::numeric, 0)
          FROM kalshi_books_current b WHERE b.ticker = ANY(f.team_tickers))
           AS kalshi_oldest_age_s,
       (SELECT string_agg(DISTINCT b.book_basis, ',') FROM kalshi_books_current b
         WHERE b.ticker = ANY(f.team_tickers)) AS kalshi_basis,
       (SELECT r.parse_status FROM market_plane_rules r
         WHERE r.contract_id = f.pmus_slug) AS pmus_rules
  FROM kalshi_fixtures_current f
 WHERE f.mapping_status = 'ESTABLISHED' AND f.pmus_mapping_status = 'ESTABLISHED'
   AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
 ORDER BY f.start_at LIMIT 60;
\echo === C3. ESTABLISHED Kalshi fixtures whose PMUS mapping is not established ===
SELECT league, left(coalesce(pmus_mapping_reasons::text, '<null>'), 120) AS r,
       count(*) AS n
  FROM kalshi_fixtures_current
 WHERE mapping_status = 'ESTABLISHED'
   AND coalesce(pmus_mapping_status, '') <> 'ESTABLISHED'
   AND start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 25;
\echo === D. kalshi_books_current freshness (rows touched in 1 d) ===
SELECT book_basis, readable, count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM now() - observed_at)) AS p50_age_s,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM now() - observed_at)) AS p90_age_s
  FROM kalshi_books_current WHERE updated_at > now() - interval '1 day'
 GROUP BY 1, 2 ORDER BY 3 DESC;
\echo === E. canonical_claim_aliases touched in 1 h: venue x side x refusals ===
SELECT venue, side, left(refusals::text, 160) AS refusals, count(*) AS n,
       count(*) FILTER (WHERE observed_at IS NOT NULL) AS with_book
  FROM canonical_claim_aliases WHERE updated_at > now() - interval '1 hour'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;
\echo === F. PMUS totals / spreads in the registry starting in (-4 h, +36 h), with a recorded book in 15 min ===
SELECT r.market_type, r.family, r.period, count(*) AS n,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM paper_book_observations o
          WHERE o.us_market_slug = r.contract_id
            AND o.observed_at > now() - interval '15 minutes')) AS with_book_15m
  FROM market_plane_registry r
 WHERE r.venue = 'POLYMARKET_US' AND r.active
   AND r.event_start BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
   AND (r.contract_id LIKE 'tsc-%' OR r.contract_id LIKE 'asc-%')
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;
\echo === F2. sample tsc / asc slugs per market_type (grammar check) ===
SELECT r.market_type, min(r.contract_id) AS slug_a, max(r.contract_id) AS slug_b,
       count(*) AS n
  FROM market_plane_registry r
 WHERE r.venue = 'POLYMARKET_US' AND r.active
   AND (r.contract_id LIKE 'tsc-%' OR r.contract_id LIKE 'asc-%')
 GROUP BY 1 ORDER BY 4 DESC LIMIT 40;
\echo === G. PMUS published void / postponement clause by market_type (active, game lines and winners) ===
SELECT reg.market_type,
       left(substring(r.rules_text from '(?i)(delayed|postponed|cancel+ed|suspended)[^.]*\.'), 260) AS clause,
       count(*) AS n
  FROM market_plane_rules r
  JOIN market_plane_registry reg ON reg.contract_id = r.contract_id
 WHERE r.venue = 'POLYMARKET_US' AND reg.active
   AND (r.contract_id LIKE 'tsc-%' OR r.contract_id LIKE 'asc-%'
        OR r.contract_id LIKE 'aec-%')
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 60;
\echo === G2. KALSHI game-series void / postponement clause (rules_secondary) by series ===
SELECT split_part(substring(r.contract_id from 7), '-', 1) AS series,
       left(substring(coalesce(r.rules_secondary, '') from '(?i)(postpone|cancel|suspend|delay)[^.]*\.'), 260) AS clause,
       count(*) AS n
  FROM market_plane_rules r
 WHERE r.venue = 'KALSHI'
   AND r.contract_id IN (SELECT 'kalshi:' || unnest(team_tickers)
                           FROM kalshi_fixtures_current
                          WHERE start_at > now() - interval '4 hours')
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;
\echo === H. ingestion_state kalshi_contract_terms (rulebooks) ===
SELECT left(value::text, 2500) FROM ingestion_state WHERE key = 'kalshi_contract_terms';
\echo === I. latest census refusals by primary code (newest scan of each kind) ===
SELECT s.scan_id, f.primary_code, count(*) AS n
  FROM adriana_arb_refusals f
  JOIN (SELECT DISTINCT ON (split_part(scan_id, '-', 2)) scan_id
          FROM adriana_arb_scans ORDER BY split_part(scan_id, '-', 2), started_at DESC) s
    ON s.scan_id = f.scan_id
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;
