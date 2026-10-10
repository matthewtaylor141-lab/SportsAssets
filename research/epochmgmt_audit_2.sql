-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 2:
-- positions carried across the 2026-10-05 00:00 America/New_York epoch
-- (2026-10-05 04:00:00 UTC) and whether each has a defensible epoch mark.
\echo == A ledger cash and reserved at the epoch (entries committed before it, plus the one funding)
SELECT round(sum(cash_delta_usd) FILTER (WHERE committed_at < timestamptz '2026-10-05 04:00:00+00' OR kind = 'INITIAL_FUNDING')::numeric, 6) AS cash_at_epoch,
       round(sum(reserved_delta_usd) FILTER (WHERE committed_at < timestamptz '2026-10-05 04:00:00+00')::numeric, 6) AS reserved_at_epoch,
       round(sum(cash_delta_usd)::numeric, 6) AS cash_now,
       count(*) FILTER (WHERE committed_at >= timestamptz '2026-10-05 04:00:00+00') AS entries_after_epoch,
       count(*) FILTER (WHERE committed_at < timestamptz '2026-10-05 04:00:00+00') AS entries_before_epoch
  FROM paper_ledger WHERE account_id = 'paper_acct_main';

\echo == B carried positions: held at the epoch, with the evidence for an epoch mark
WITH params AS (SELECT timestamptz '2026-10-05 04:00:00+00' AS epoch),
f AS (
  SELECT f.group_id, f.us_market_slug AS slug, f.holding_side AS side, f.direction, f.qty,
         coalesce(l.committed_at, f.recorded_at) AS at
    FROM paper_fills f
    LEFT JOIN paper_ledger l ON l.fill_id = f.fill_id AND l.kind IN ('FILL', 'SALE')
   WHERE f.account_id = 'paper_acct_main'),
q AS (
  SELECT group_id, slug, side,
         sum(CASE WHEN direction = 'BUY' THEN qty ELSE -qty END) FILTER (WHERE at < (SELECT epoch FROM params)) AS q_pre,
         sum(CASE WHEN direction = 'BUY' THEN qty ELSE -qty END) AS q_all
    FROM f GROUP BY 1, 2, 3),
pk AS (SELECT q.*, 'paperpos:paper_acct_main:' || group_id || ':' || slug || ':' || side AS position_key FROM q),
st AS (
  SELECT position_key, (array_agg(qty ORDER BY version DESC))[1] AS sq_latest
    FROM paper_settlements WHERE account_id = 'paper_acct_main' GROUP BY position_key),
sl AS (
  SELECT position_key, min(committed_at) AS first_settle_entry
    FROM paper_ledger WHERE account_id = 'paper_acct_main' AND kind = 'SETTLEMENT' GROUP BY position_key),
c AS (
  SELECT pk.*, st.sq_latest, sl.first_settle_entry,
         pk.q_pre - CASE WHEN sl.first_settle_entry < (SELECT epoch FROM params) THEN coalesce(st.sq_latest, 0) ELSE 0 END AS q_epoch,
         pk.q_all - coalesce(st.sq_latest, 0) AS q_now
    FROM pk LEFT JOIN st USING (position_key) LEFT JOIN sl USING (position_key))
SELECT c.slug, c.side, round(c.q_epoch::numeric, 4) AS q_epoch, round(c.q_now::numeric, 4) AS q_now_open,
       c.first_settle_entry,
       EXISTS (SELECT 1 FROM paper_book_observations b WHERE b.us_market_slug = c.slug AND b.error IS NULL
                  AND b.observed_at <= (SELECT epoch FROM params)
                  AND b.observed_at >= (SELECT epoch FROM params) - interval '300 seconds') AS window_book_300s,
       (SELECT max(b.observed_at) FROM paper_book_observations b WHERE b.us_market_slug = c.slug AND b.error IS NULL
           AND b.observed_at <= (SELECT epoch FROM params)) AS last_error_free_book_by_epoch,
       (SELECT b.market_state FROM paper_book_observations b WHERE b.us_market_slug = c.slug AND b.error IS NULL
           AND b.observed_at <= (SELECT epoch FROM params) ORDER BY b.observed_at DESC LIMIT 1) AS last_book_state,
       (SELECT count(*) FROM paper_book_observations b WHERE b.us_market_slug = c.slug
           AND b.observed_at <= (SELECT epoch FROM params)
           AND b.observed_at >= (SELECT epoch FROM params) - interval '300 seconds') AS reads_in_window
  FROM c WHERE c.q_epoch > 0.000001
 ORDER BY c.slug, c.side;

\echo == C summary of carried positions
WITH params AS (SELECT timestamptz '2026-10-05 04:00:00+00' AS epoch),
f AS (
  SELECT f.group_id, f.us_market_slug AS slug, f.holding_side AS side, f.direction, f.qty,
         coalesce(l.committed_at, f.recorded_at) AS at
    FROM paper_fills f
    LEFT JOIN paper_ledger l ON l.fill_id = f.fill_id AND l.kind IN ('FILL', 'SALE')
   WHERE f.account_id = 'paper_acct_main'),
q AS (
  SELECT group_id, slug, side,
         sum(CASE WHEN direction = 'BUY' THEN qty ELSE -qty END) FILTER (WHERE at < (SELECT epoch FROM params)) AS q_pre
    FROM f GROUP BY 1, 2, 3),
pk AS (SELECT q.*, 'paperpos:paper_acct_main:' || group_id || ':' || slug || ':' || side AS position_key FROM q),
st AS (
  SELECT position_key, (array_agg(qty ORDER BY version DESC))[1] AS sq_latest
    FROM paper_settlements WHERE account_id = 'paper_acct_main' GROUP BY position_key),
sl AS (
  SELECT position_key, min(committed_at) AS first_settle_entry
    FROM paper_ledger WHERE account_id = 'paper_acct_main' AND kind = 'SETTLEMENT' GROUP BY position_key),
c AS (
  SELECT pk.*, pk.q_pre - CASE WHEN sl.first_settle_entry < (SELECT epoch FROM params) THEN coalesce(st.sq_latest, 0) ELSE 0 END AS q_epoch
    FROM pk LEFT JOIN st USING (position_key) LEFT JOIN sl USING (position_key))
SELECT count(*) FILTER (WHERE q_epoch > 0.000001) AS carried_positions,
       count(*) FILTER (WHERE q_epoch > 0.000001 AND EXISTS (
           SELECT 1 FROM paper_book_observations b WHERE b.us_market_slug = c.slug AND b.error IS NULL
              AND b.observed_at <= (SELECT epoch FROM params)
              AND b.observed_at >= (SELECT epoch FROM params) - interval '300 seconds')) AS with_a_window_book,
       count(*) FILTER (WHERE q_epoch > 0.000001 AND NOT EXISTS (
           SELECT 1 FROM paper_book_observations b WHERE b.us_market_slug = c.slug AND b.error IS NULL
              AND b.observed_at <= (SELECT epoch FROM params)
              AND b.observed_at >= (SELECT epoch FROM params) - interval '300 seconds')) AS without_a_window_book
  FROM c;

\echo == D service mark refresh coverage on the epoch night: error-free books per hour around the epoch for the held slugs
SELECT date_trunc('hour', observed_at) AS hour_utc, count(*) AS reads, count(*) FILTER (WHERE error IS NULL) AS error_free,
       count(DISTINCT us_market_slug) AS slugs
  FROM paper_book_observations
 WHERE observed_at >= timestamptz '2026-10-05 00:00:00+00' AND observed_at < timestamptz '2026-10-05 08:00:00+00'
 GROUP BY 1 ORDER BY 1;
