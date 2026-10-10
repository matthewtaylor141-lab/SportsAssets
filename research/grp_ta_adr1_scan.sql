-- READ-ONLY. Root-cause audit, group truth-agents, part 3: Adriana. Why the
-- claim-first scan reads 404 fresh books of 462 aliases (the 58 Polymarket US
-- aliases are all without a fresh book), why 252 of 462 aliases state no
-- postponement payout, and why the recorded-books census reads no contract.
-- SELECT only.

\echo == 1 claim-first scans (adr-claims-): newest 8, with the per-venue book split
SELECT scan_id, to_timestamp(split_part(scan_id, '-', 3)::numeric / 1000) AS at, status, markets_read, books_fresh,
       by_code -> 'book_sources' -> 'KALSHI' ->> 'fresh' AS kalshi_fresh,
       by_code -> 'book_sources' -> 'KALSHI' ->> 'aliases' AS kalshi_aliases,
       by_code -> 'book_sources' -> 'POLYMARKET_US' ->> 'fresh' AS pmus_fresh,
       by_code -> 'book_sources' -> 'POLYMARKET_US' ->> 'aliases' AS pmus_aliases,
       by_code -> 'void_terms' ->> 'established' AS vt_established,
       by_code -> 'void_terms' ->> 'aliases' AS vt_aliases,
       by_code -> 'scope' ->> 'cut_by_cap' AS cut_by_cap,
       by_code -> 'scope' ->> 'in_window' AS in_window
  FROM adriana_arb_scans WHERE scan_id LIKE 'adr-claims-%'
 ORDER BY started_at DESC LIMIT 8;

\echo == 2 claim scans over the last 24 h: PMUS fresh aliases by hour (avg and max)
SELECT date_trunc('hour', started_at) AS hr, count(*) AS scans,
       round(avg((by_code -> 'book_sources' -> 'POLYMARKET_US' ->> 'fresh')::numeric), 1) AS pmus_fresh_avg,
       max((by_code -> 'book_sources' -> 'POLYMARKET_US' ->> 'fresh')::int) AS pmus_fresh_max,
       round(avg((by_code -> 'book_sources' -> 'POLYMARKET_US' ->> 'aliases')::numeric), 1) AS pmus_aliases_avg,
       round(avg(books_fresh::numeric / nullif(markets_read, 0)), 4) AS fresh_rate_avg
  FROM adriana_arb_scans WHERE scan_id LIKE 'adr-claims-%' AND started_at >= now() - interval '24 hours'
 GROUP BY 1 ORDER BY 1 DESC LIMIT 26;

\echo == 3 census scans (adr-scan-): status and markets read, newest 6 and the 24 h split
SELECT scan_id, status, why, markets_read, books_fresh FROM adriana_arb_scans WHERE scan_id LIKE 'adr-scan-%'
 ORDER BY started_at DESC LIMIT 6;
SELECT status, count(*) AS n, min(started_at) AS first_at, max(started_at) AS last_at
  FROM adriana_arb_scans WHERE scan_id LIKE 'adr-scan-%' AND started_at >= now() - interval '48 hours'
 GROUP BY 1 ORDER BY 1;

\echo == 4 paper_book_observations: the only PMUS book source of both scans
SELECT count(*) FILTER (WHERE observed_at > now() - interval '900 seconds') AS rows_last_900s,
       count(*) FILTER (WHERE observed_at > now() - interval '3600 seconds') AS rows_last_1h,
       count(*) FILTER (WHERE observed_at > now() - interval '24 hours') AS rows_last_24h,
       max(observed_at) AS newest, round(extract(epoch FROM (now() - max(observed_at)))::numeric / 3600, 2) AS newest_age_h
  FROM paper_book_observations;
SELECT date_trunc('hour', observed_at) AS hr, source, count(*) AS rows, count(*) FILTER (WHERE error IS NULL) AS rows_ok,
       count(DISTINCT us_market_slug) AS slugs
  FROM paper_book_observations WHERE observed_at >= now() - interval '30 hours'
 GROUP BY 1, 2 ORDER BY 1 DESC, 2 LIMIT 40;

\echo == 5 the 29 PMUS markets mapped to Kalshi fixtures now: newest recorded paper book per slug
SELECT f.pmus_slug, (SELECT max(observed_at) FROM paper_book_observations o WHERE o.us_market_slug = f.pmus_slug AND o.error IS NULL) AS newest_ok_book,
       (SELECT count(*) FROM paper_book_observations o WHERE o.us_market_slug = f.pmus_slug AND o.observed_at > now() - interval '24 hours') AS rows_24h
  FROM kalshi_fixtures_current f
 WHERE f.mapping_status = 'ESTABLISHED' AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
   AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
 ORDER BY f.start_at LIMIT 40;

\echo == 6 in-window fixtures in the scan order: first 80 versus the rest, by series (the cap)
WITH win AS (
  SELECT f.event_ticker, f.series_ticker, f.start_at, f.team_tickers, f.tie_ticker, f.pmus_slug,
         coalesce(f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL, false) AS xv,
         coalesce(EXISTS (SELECT 1 FROM kalshi_books_current b WHERE b.readable AND (b.ticker = ANY(f.team_tickers) OR b.ticker = f.tie_ticker)), false) AS rd
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED' AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
), ranked AS (
  SELECT *, row_number() OVER (ORDER BY xv DESC, rd DESC, start_at, event_ticker) AS rn FROM win)
SELECT series_ticker, count(*) AS fixtures, count(*) FILTER (WHERE rn <= 80) AS read_by_scan,
       count(*) FILTER (WHERE rn > 80) AS cut_by_cap, count(*) FILTER (WHERE xv) AS cross_venue
  FROM ranked GROUP BY 1 ORDER BY 2 DESC LIMIT 40;

\echo == 7 rules evidence of the Kalshi tickers of the first 80 fixtures, by series: what is stated
WITH win AS (
  SELECT f.event_ticker, f.series_ticker, f.start_at, f.team_tickers, f.tie_ticker,
         coalesce(f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL, false) AS xv,
         coalesce(EXISTS (SELECT 1 FROM kalshi_books_current b WHERE b.readable AND (b.ticker = ANY(f.team_tickers) OR b.ticker = f.tie_ticker)), false) AS rd
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED' AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
), ranked AS (
  SELECT *, row_number() OVER (ORDER BY xv DESC, rd DESC, start_at, event_ticker) AS rn FROM win),
tk AS (
  SELECT r.series_ticker, t.ticker FROM ranked r, LATERAL unnest(r.team_tickers || CASE WHEN r.tie_ticker IS NULL THEN ARRAY[]::text[] ELSE ARRAY[r.tie_ticker] END) AS t(ticker)
   WHERE r.rn <= 80)
SELECT tk.series_ticker, count(*) AS tickers, count(m.contract_id) AS with_rules_row,
       count(*) FILTER (WHERE m.evidence -> 'settlement' ->> 'void_rule' IS NOT NULL) AS void_rule_stated,
       count(*) FILTER (WHERE m.evidence -> 'settlement' ->> 'postponement_payout' IS NOT NULL) AS postponement_payout_stated,
       count(*) FILTER (WHERE m.evidence -> 'settlement' ->> 'postponement_window_hours' IS NOT NULL) AS window_stated,
       count(*) FILTER (WHERE (coalesce(m.rules_text, '') || ' ' || coalesce(m.rules_secondary, '')) ~* 'within (two|2) days') AS text_has_two_day_window,
       count(*) FILTER (WHERE (coalesce(m.rules_text, '') || ' ' || coalesce(m.rules_secondary, '')) ~* '(over|further than) (two|2) days[^.]*fair (market )?price') AS text_has_beyond_clause
  FROM tk LEFT JOIN market_plane_rules m ON m.contract_id = 'kalshi:' || tk.ticker
 GROUP BY 1 ORDER BY 2 DESC LIMIT 40;

\echo == 8 rules evidence of the mapped PMUS slugs
SELECT m.venue, m.parse_status, m.rules_published, count(*) AS n,
       count(*) FILTER (WHERE m.evidence -> 'settlement' ->> 'void_rule' IS NOT NULL) AS void_rule_stated,
       count(*) FILTER (WHERE m.evidence -> 'settlement' ->> 'postponement_payout' IS NOT NULL) AS postponement_payout_stated,
       count(*) FILTER (WHERE m.evidence -> 'settlement' ->> 'postponement_window_hours' IS NOT NULL) AS window_stated
  FROM market_plane_rules m
 WHERE m.contract_id IN (SELECT f.pmus_slug FROM kalshi_fixtures_current f
                          WHERE f.mapping_status = 'ESTABLISHED' AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
                            AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours')
 GROUP BY 1, 2, 3 ORDER BY 1, 2;

\echo == 9 one example PMUS rules text head and its parsed settlement keys (no secrets)
SELECT m.contract_id, left(m.rules_text, 600) AS rules_head, m.evidence -> 'settlement' AS settlement
  FROM market_plane_rules m
 WHERE m.contract_id IN (SELECT f.pmus_slug FROM kalshi_fixtures_current f
                          WHERE f.mapping_status = 'ESTABLISHED' AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
                            AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours')
 ORDER BY m.contract_id LIMIT 2;

\echo == 10 one example Kalshi ticker per series without the beyond clause: text head
WITH win AS (
  SELECT f.series_ticker, f.team_tickers FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED' AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'),
tk AS (SELECT DISTINCT ON (w.series_ticker) w.series_ticker, w.team_tickers[1] AS ticker FROM win w ORDER BY w.series_ticker)
SELECT tk.series_ticker, tk.ticker, m.parse_status,
       ((coalesce(m.rules_text, '') || ' ' || coalesce(m.rules_secondary, '')) ~* '(over|further than) (two|2) days[^.]*fair (market )?price') AS has_beyond_clause,
       left(coalesce(m.rules_text, ''), 220) AS rules_head, left(coalesce(m.rules_secondary, ''), 420) AS secondary_head
  FROM tk LEFT JOIN market_plane_rules m ON m.contract_id = 'kalshi:' || tk.ticker
 ORDER BY tk.series_ticker LIMIT 30;
