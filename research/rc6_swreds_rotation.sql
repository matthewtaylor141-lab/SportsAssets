-- READ-ONLY. RC6 lane C (software reds): does the scarce fresh window of each
-- metered fetch rotate across a competition's events (REQUEUE_AFTER_OUR_
-- DELAY_RULE) or go to the same events? Per competition and hour since
-- 2026-10-08 16:00Z: metered cycles, rows that reached a venue book read
-- (any book-read outcome) and the DISTINCT events among them, rows refused on
-- arrival, and the hourly first-loss shape. Then, per NCAAF event, how many
-- cycles it was read in and how many it was refused on arrival. SELECT only.

\echo R1 per competition and hour: read rows vs distinct read events vs arrival refusals
WITH c AS (SELECT cycle_id, count(*) AS n FROM ext_candidate_outcomes
            WHERE cycle_at >= timestamptz '2026-10-08 16:00:00+00' GROUP BY cycle_id)
SELECT date_trunc('hour', o.cycle_at) AS hour, o.sport_key,
       count(DISTINCT o.cycle_id) AS cycles,
       count(DISTINCT o.provider_event_id) FILTER (WHERE o.us_market_slug IS NOT NULL) AS mapped_events,
       count(*) FILTER (WHERE o.first_refusal IN (
           'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED', 'VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT',
           'VENUE_BOOK_READ_FAILED', 'VENUE_BOOK_READ_RETURNED_ERROR',
           'PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH')
           OR o.outcome IN ('ADMITTED', 'ALREADY_RECORDED')) AS read_rows,
       count(DISTINCT o.provider_event_id) FILTER (WHERE o.first_refusal IN (
           'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED', 'VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT',
           'VENUE_BOOK_READ_FAILED', 'VENUE_BOOK_READ_RETURNED_ERROR')
           OR o.outcome IN ('ADMITTED', 'ALREADY_RECORDED')) AS events_with_a_book,
       count(*) FILTER (WHERE o.first_refusal = 'PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH') AS pdl_rows,
       count(*) FILTER (WHERE o.first_refusal = 'QUOTE_STALE_ON_ARRIVAL') AS qsoa_rows
  FROM ext_candidate_outcomes o JOIN c USING (cycle_id)
 WHERE o.cycle_at >= timestamptz '2026-10-08 16:00:00+00' AND c.n > 1
   AND o.sport_key IN ('americanfootball_ncaaf', 'americanfootball_nfl', 'soccer_brazil_serie_b', 'baseball_mlb', 'soccer_mexico_ligamx')
 GROUP BY 1, 2 ORDER BY 2, 1;

\echo R2 NCAAF events since 19:00Z: cycles in which each got a book, cycles refused on arrival, first and last book
WITH c AS (SELECT cycle_id, count(*) AS n FROM ext_candidate_outcomes
            WHERE cycle_at >= timestamptz '2026-10-08 19:00:00+00' GROUP BY cycle_id)
SELECT left(o.us_market_slug, 32) AS slug,
       count(DISTINCT o.cycle_id) FILTER (WHERE o.first_refusal IN (
           'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED', 'VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT',
           'VENUE_BOOK_READ_FAILED', 'VENUE_BOOK_READ_RETURNED_ERROR')) AS book_cycles,
       count(DISTINCT o.cycle_id) FILTER (WHERE o.first_refusal = 'PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH') AS pdl_cycles,
       count(DISTINCT o.cycle_id) FILTER (WHERE o.first_refusal = 'QUOTE_STALE_ON_ARRIVAL') AS qsoa_cycles,
       min(o.queue_position) AS qp_min, max(o.queue_position) AS qp_max,
       to_char(min(o.cycle_at) FILTER (WHERE o.first_refusal LIKE 'VENUE_BOOK%'), 'HH24:MI') AS first_book,
       to_char(max(o.cycle_at) FILTER (WHERE o.first_refusal LIKE 'VENUE_BOOK%'), 'HH24:MI') AS last_book
  FROM ext_candidate_outcomes o JOIN c USING (cycle_id)
 WHERE o.cycle_at >= timestamptz '2026-10-08 19:00:00+00' AND c.n > 1
   AND o.sport_key = 'americanfootball_ncaaf' AND o.us_market_slug IS NOT NULL
 GROUP BY 1 ORDER BY book_cycles DESC, 1
 LIMIT 70;

\echo R3 the queue head of every NCAAF metered cycle since 19:00Z (first 9 positions)
SELECT to_char(o.cycle_at, 'HH24:MI') AS cyc, o.queue_position AS qp,
       left(o.us_market_slug, 30) AS slug, left(coalesce(o.first_refusal, o.outcome), 40) AS code,
       round(o.provider_lag_s::numeric, 1) AS lag, round(o.our_processing_s::numeric, 1) AS ours
  FROM ext_candidate_outcomes o
 WHERE o.cycle_at >= timestamptz '2026-10-08 19:00:00+00'
   AND o.sport_key = 'americanfootball_ncaaf' AND o.queue_position < 9
   AND o.cycle_id IN (SELECT cycle_id FROM ext_candidate_outcomes
                       WHERE cycle_at >= timestamptz '2026-10-08 19:00:00+00'
                       GROUP BY cycle_id HAVING count(*) > 1)
 ORDER BY o.cycle_at, o.queue_position
 LIMIT 260;
