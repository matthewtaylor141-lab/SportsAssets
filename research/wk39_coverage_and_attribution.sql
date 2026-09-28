-- READ-ONLY. THE THREE THINGS THE WK39 REPORT ASSERTED WITHOUT ESTABLISHING.
--
-- Owner corrections to the weekly report:
--
--   "Attribute figures to the actual account, lane and strategy, keeping
--    legacy results separate from the new autonomous EV engine."
--   "Do not infer complete coverage from a recent crawl or the newest date
--    being present. Check pagination, historical coverage and whether
--    missing dates represent no activity or unavailable records."
--
-- THE PUBLISHED EDITION SAID "absent after a fresh crawl" FOR SEP 21-22 AND
-- CALLED THAT "Absent, not zero, and not stale". That is a claim about
-- WHY two days are missing, and a fresh crawl does not establish it: a
-- crawl that paginates and stops early produces exactly the same symptom
-- as two days with no activity. This query separates them.
--
-- THE DISCRIMINATOR. `venue_truth_days` is the crawl's output.
-- `live_orders` is our own order record, written when WE act, and it does
-- not depend on the crawl at all. So:
--
--     orders settled on a day WITH no venue_truth_days row
--        => the record is UNAVAILABLE (a crawl gap). Activity existed.
--     no orders settled on that day EITHER
--        => consistent with NO ACTIVITY, though still not proof, because
--           both sources could miss the same day for the same reason.
--
-- That asymmetry is stated in the output rather than resolved by
-- assertion: one direction is decisive, the other is only consistent.

-- ─────────────────────────────────────────────────────────────────────
-- 1 · HISTORICAL COVERAGE, not just the newest date. How far back the
--     crawl reaches, how many distinct days it holds, and how many days
--     the span WOULD contain if it were complete.
-- ─────────────────────────────────────────────────────────────────────
SELECT min(day)                                   AS earliest_day,
       max(day)                                   AS latest_day,
       count(DISTINCT day)                        AS distinct_days_present,
       (max(day)::date - min(day)::date) + 1      AS days_in_the_span,
       ((max(day)::date - min(day)::date) + 1)
         - count(DISTINCT day)                    AS days_missing_in_the_span,
       count(*)                                   AS rows_total,
       count(DISTINCT venue)                      AS venues,
       max(updated_at)                            AS crawl_last_wrote
  FROM venue_truth_days;

-- ─────────────────────────────────────────────────────────────────────
-- 2 · EVERY missing day in the whole span, named. A report that mentions
--     only the two inside its own week would present a local gap as if
--     the rest of the history were dense.
-- ─────────────────────────────────────────────────────────────────────
WITH span AS (
    SELECT min(day)::date AS a, max(day)::date AS b FROM venue_truth_days
), all_days AS (
    SELECT generate_series(a, b, INTERVAL '1 day')::date AS d FROM span
), present AS (
    SELECT DISTINCT day::date AS d FROM venue_truth_days
)
SELECT to_char(a.d, 'YYYY-MM-DD') AS missing_day,
       to_char(a.d, 'Dy')         AS weekday
  FROM all_days a LEFT JOIN present p ON p.d = a.d
 WHERE p.d IS NULL
 ORDER BY a.d;

-- ─────────────────────────────────────────────────────────────────────
-- 3 · THE DISCRIMINATOR. For every missing day, did WE settle anything?
--     `live_orders` is our own record and does not come from the crawl.
-- ─────────────────────────────────────────────────────────────────────
WITH span AS (
    SELECT min(day)::date AS a, max(day)::date AS b FROM venue_truth_days
), all_days AS (
    SELECT generate_series(a, b, INTERVAL '1 day')::date AS d FROM span
), present AS (
    SELECT DISTINCT day::date AS d FROM venue_truth_days
), missing AS (
    SELECT a.d FROM all_days a LEFT JOIN present p ON p.d = a.d
     WHERE p.d IS NULL
)
SELECT to_char(m.d, 'YYYY-MM-DD')                       AS missing_day,
       count(o.*)                                       AS our_orders_settled,
       count(o.*) FILTER (WHERE o.pnl IS NOT NULL)      AS with_a_pnl,
       round(coalesce(sum(o.pnl), 0)::numeric, 2)       AS our_pnl_that_day,
       CASE WHEN count(o.*) > 0
            THEN 'UNAVAILABLE_RECORD__WE_TRADED_AND_THE_CRAWL_HAS_NO_ROW'
            ELSE 'CONSISTENT_WITH_NO_ACTIVITY__NOT_PROOF_OF_IT'
       END                                              AS verdict
  FROM missing m
  LEFT JOIN live_orders o
         ON o.settled_at >= (m.d::timestamptz + INTERVAL '4 hours')
        AND o.settled_at <  (m.d::timestamptz + INTERVAL '28 hours')
 GROUP BY m.d
 ORDER BY m.d;

-- ─────────────────────────────────────────────────────────────────────
-- 4 · ATTRIBUTION. The published −$794.01 carries no account, lane or
--     strategy. This prints the week's settled P&L BY LANE AND VENUE so
--     legacy cannot be folded in with the autonomous EV engine.
--
--     THE EXPECTED RESULT, stated before reading it so the query is not
--     fitted to the answer: the funded book holds 0 intents and no order
--     has ever been sent by the EV lane, so the whole figure should be
--     legacy. If any of it is attributed to the EV lane, the report's
--     framing is wrong and so is my account of the system.
-- ─────────────────────────────────────────────────────────────────────
SELECT coalesce(lane, '(null lane)')              AS lane,
       coalesce(venue, '(null venue)')            AS venue,
       coalesce(whale_username, '(no sleeve)')    AS sleeve,
       count(*)                                   AS orders,
       count(*) FILTER (WHERE settled_at IS NOT NULL) AS settled,
       round(coalesce(sum(pnl), 0)::numeric, 2)   AS realized_usd,
       round(coalesce(sum(filled_usd), 0)::numeric, 2) AS filled_usd,
       min(settled_at)                            AS first_settled,
       max(settled_at)                            AS last_settled
  FROM live_orders
 WHERE settled_at >= TIMESTAMPTZ '2026-09-21 04:00:00+00'
   AND settled_at <  TIMESTAMPTZ '2026-09-28 04:00:00+00'
 GROUP BY 1, 2, 3
 ORDER BY realized_usd;

-- ─────────────────────────────────────────────────────────────────────
-- 5 · AND THE SAME ATTRIBUTION OVER ALL TIME, so "the EV engine has
--     never traded" is a measurement rather than a recollection.
-- ─────────────────────────────────────────────────────────────────────
SELECT coalesce(lane, '(null lane)')              AS lane,
       count(*)                                   AS orders_all_time,
       count(*) FILTER (WHERE settled_at IS NOT NULL) AS settled_all_time,
       round(coalesce(sum(pnl), 0)::numeric, 2)   AS realized_all_time,
       min(settled_at)                            AS first_ever,
       max(settled_at)                            AS last_ever
  FROM live_orders
 GROUP BY 1
 ORDER BY orders_all_time DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 6 · THE FUNDED BOOK, counted rather than asserted. The autonomous EV
--     engine's own ledger. If this is empty, no part of any weekly figure
--     can belong to it.
-- ─────────────────────────────────────────────────────────────────────
SELECT count(*)                                            AS funded_intents,
       count(*) FILTER (WHERE kind = 'ENTRY')              AS entries,
       count(*) FILTER (WHERE closed_at IS NOT NULL)       AS closed,
       coalesce(sum(residual_qty), 0)                      AS residual_contracts
  FROM bettor_funded_intents;

-- ─────────────────────────────────────────────────────────────────────
-- 7 · PAGINATION / CRAWL STATE. Whether the crawl records how far it got.
--     If it keeps no cursor or page count, then "a fresh crawl ran" cannot
--     support a completeness claim at all, and that is the finding.
-- ─────────────────────────────────────────────────────────────────────
SELECT key,
       left(value::text, 400) AS value_head,
       length(value::text)    AS value_len
  FROM ingestion_state
 WHERE key ILIKE '%venue_truth%'
    OR key ILIKE '%crawl%'
    OR key ILIKE '%cursor%'
    OR key ILIKE '%page%'
    OR key ILIKE '%probe%'
    OR key ILIKE '%backfill%'
 ORDER BY key;

-- ─────────────────────────────────────────────────────────────────────
-- 8 · PER-DAY DETAIL FOR THE PUBLISHED WEEK, one row per (day, venue),
--     so the subtotal can be recomputed from its parts by a reader.
-- ─────────────────────────────────────────────────────────────────────
SELECT day, venue, settled, wins, losses,
       round(cost::numeric, 2)     AS cost,
       round(realized::numeric, 2) AS realized,
       updated_at
  FROM venue_truth_days
 WHERE day >= '2026-09-21' AND day <= '2026-09-27'
 ORDER BY day, venue;
