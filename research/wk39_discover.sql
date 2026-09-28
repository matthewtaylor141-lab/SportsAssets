-- READ-ONLY discovery for the wk39 weekly report: the exact columns the
-- venue-truth and copy-sleeve anchors are computed from. Guessing a column
-- name would silently change a published financial figure.

SELECT table_name, ordinal_position, column_name, data_type
  FROM information_schema.columns
 WHERE table_schema = 'public'
   AND table_name IN ('venue_truth_days', 'live_orders')
 ORDER BY table_name, ordinal_position;

-- Freshness of the venue-truth crawl, which wk38 reported explicitly.
SELECT max(day) AS max_day, max(updated_at) AS max_updated_at, count(*) AS rows_
  FROM venue_truth_days;

-- The days present in the week that just ended (ET Mon 09-21 -> Sun 09-27).
SELECT day, count(*) AS rows_
  FROM venue_truth_days
 WHERE day BETWEEN DATE '2026-09-21' AND DATE '2026-09-27'
 GROUP BY day ORDER BY day;
