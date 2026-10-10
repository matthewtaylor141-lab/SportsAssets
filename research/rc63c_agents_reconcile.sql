-- RC6.3c PROOF E: AUDREY, XAVIER AND ALLIE RECONCILIATION, AFTER THE DEPLOY AND THE HOUR BEFORE IT (SELECT only).
--
-- PURPOSE. Prove the three agents' reconciliation is unchanged by RC6.3c, from their own records, over two windows:
-- AFTER = [2026-10-10 18:00:00+00, now()) and BEFORE = [2026-10-10 18:00:00+00 - 60 min, 2026-10-10 18:00:00+00) as the baseline.
--   AUDREY (agents/paper_audrey.py). A reporting day (America/New_York) with paper ACTIVITY (ledger entries,
--     decisions, orders, fills, settlements, xavier reviews -- the ACTIVITY tuple) must carry a report; a completed
--     day ONE final version; versions are written only when the content changes, at most every REPORT_EVERY_S =
--     900 s; a day with activity and no report is named AUDREY_DAY_NOT_RECONCILED. Coverage = days with activity
--     that have a report / days with activity: expected 1.0; actual below. The report's `reconciles` is the AND of
--     its checks (ACQUISITION_EQUALS_LEDGER_FILL_DEBITS, SALE_PROCEEDS_EQUAL_LEDGER_SALE_CREDITS,
--     CASH_EQUALS_THE_LEDGER_SUM, SETTLEMENTS_EQUAL_LEDGER_SETTLEMENT_AND_CORRECTIONS, RUNNING_BALANCES_AGREE,
--     EVERY_FILL_HAS_ONE_ROLE): the same checks are recomputed here, all time, as of now (E2).
--   XAVIER (agents/paper_xavier.py step). Every held (canonically open) group handed off (paper_handoffs) is
--     reviewed when due: first fill, fill event, market event, or the scheduled backstop xavier_backstop_s = 60 s
--     after the last review; reviews are paper_xavier_reviews rows. Owed in a window ~= open handed groups x
--     window / 60 s; done = reviews written in the window. A held position with NO review at all, or none in a
--     window longer than two passes, is a reconciliation failure (xavier_management.summary open_without_review).
--   ALLIE (open_position_canon.py, RC6.3c allie-exposure). Her book / fixture exposure inputs read
--     OPEN_EXPOSURE_ROWS_SQL: the canonical open quantity (bought - sold - the latest settlement version's qty, per
--     account / group / contract / side, kept above 1e-9) scoped INSIDE the scans by account, times the BUY cost per
--     contract incl. fees. E4 runs the canonical statement (CANONICAL_OPEN_POSITIONS_SQL, verbatim), the exposure
--     statement for every account (NULL) and per account (the literal account inside the scans), and the ledger's
--     own POSITIONS_SQL arithmetic per account, and counts positions where any of them disagree on open quantity.
--
-- PLACEHOLDER. 2026-10-10 18:00:00+00 : the deploy instant (ISO timestamp with zone), e.g. 2026-10-10 18:00:00+00.
--
-- PASS CRITERIA. E1: coverage_ratio = 1.0 for completed days (final versions) and today's newest version within
-- 900 s + one pass of the newest activity (or no activity since it); no AUDREY_DAY_NOT_RECONCILED finding after the
-- deploy. E2: every recomputed check true (fills_without_ledger_entry = 0, ledger_fill_entries_without_fill = 0,
-- cash_matches, settlements_match, running_balances_consistent). E3: open_handed_without_review_in_window = 0 for
-- the AFTER window once it is longer than two passes (and the BEFORE window), open_without_any_review = 0,
-- reviews_done > 0 whenever open_handed_groups > 0, and the AFTER rate per open group per minute comparable to
-- BEFORE. E4: positions_disagreeing = 0 (canonical vs exposure-all vs exposure-per-account vs ledger arithmetic).

\echo E0 the windows
SELECT TIMESTAMPTZ '2026-10-10 18:00:00+00' - interval '60 minutes' AS before_start, TIMESTAMPTZ '2026-10-10 18:00:00+00' AS deploy_at, now() AS read_at,
       round((extract(epoch FROM now() - TIMESTAMPTZ '2026-10-10 18:00:00+00') / 60.0)::numeric, 1) AS after_window_min;

\echo E1a AUDREY coverage: every reporting day (America/New_York) with activity in the last 4 days: activity by record, report versions, newest version, final, reconciles
WITH days AS (
  SELECT d::date AS report_day FROM generate_series((now() AT TIME ZONE 'America/New_York')::date - 3, (now() AT TIME ZONE 'America/New_York')::date, interval '1 day') d),
act AS (
  SELECT (committed_at AT TIME ZONE 'America/New_York')::date AS report_day, 'ledger' AS rec, count(*) AS n, max(committed_at) AS newest FROM paper_ledger GROUP BY 1
  UNION ALL SELECT (decided_at AT TIME ZONE 'America/New_York')::date, 'decisions', count(*), max(decided_at) FROM paper_decisions WHERE decided_at >= now() - interval '5 days' GROUP BY 1
  UNION ALL SELECT (decided_at AT TIME ZONE 'America/New_York')::date, 'orders', count(*), max(decided_at) FROM paper_orders GROUP BY 1
  UNION ALL SELECT (filled_at AT TIME ZONE 'America/New_York')::date, 'fills', count(*), max(filled_at) FROM paper_fills GROUP BY 1
  UNION ALL SELECT (settled_at AT TIME ZONE 'America/New_York')::date, 'settlements', count(*), max(settled_at) FROM paper_settlements GROUP BY 1
  UNION ALL SELECT (reviewed_at AT TIME ZONE 'America/New_York')::date, 'xavier_reviews', count(*), max(reviewed_at) FROM paper_xavier_reviews WHERE reviewed_at >= now() - interval '5 days' GROUP BY 1),
a AS (
  SELECT report_day, sum(n) FILTER (WHERE rec = 'ledger') AS ledger, sum(n) FILTER (WHERE rec = 'decisions') AS decisions,
         sum(n) FILTER (WHERE rec = 'orders') AS orders, sum(n) FILTER (WHERE rec = 'fills') AS fills,
         sum(n) FILTER (WHERE rec = 'settlements') AS settlements, sum(n) FILTER (WHERE rec = 'xavier_reviews') AS reviews,
         max(newest) AS newest_activity
    FROM act GROUP BY 1),
r AS (
  SELECT report_day, count(*) AS versions, max(version) AS newest_version, max(generated_at) AS newest_generated,
         bool_or(final) AS has_final, bool_and(reconciles) AS all_reconcile,
         (array_agg(reconciles ORDER BY version DESC))[1] AS newest_reconciles
    FROM paper_audrey_reports GROUP BY 1)
SELECT d.report_day, coalesce(a.ledger, 0) AS ledger, coalesce(a.decisions, 0) AS decisions, coalesce(a.orders, 0) AS orders, coalesce(a.fills, 0) AS fills,
       coalesce(a.settlements, 0) AS settlements, coalesce(a.reviews, 0) AS reviews,
       to_char(a.newest_activity, 'MM-DD HH24:MI:SS') AS newest_activity,
       coalesce(r.versions, 0) AS versions, r.newest_version, to_char(r.newest_generated, 'MM-DD HH24:MI:SS') AS newest_generated,
       r.has_final, r.newest_reconciles, r.all_reconcile,
       CASE WHEN a.report_day IS NULL THEN 'no activity'
            WHEN r.report_day IS NULL THEN 'ACTIVITY_WITHOUT_REPORT'
            WHEN d.report_day < (now() AT TIME ZONE 'America/New_York')::date AND NOT r.has_final THEN 'COMPLETED_DAY_NOT_FINAL'
            WHEN a.newest_activity > r.newest_generated + interval '900 seconds' + interval '120 seconds' THEN 'ACTIVITY_AFTER_NEWEST_VERSION_BY_OVER_900S'
            ELSE 'covered' END AS coverage
  FROM days d LEFT JOIN a USING (report_day) LEFT JOIN r USING (report_day)
 ORDER BY d.report_day;

\echo E1b AUDREY coverage ratio over the last 30 days (expected 1.0): days with activity, days with a report, completed days with a final version
WITH act AS (
  SELECT DISTINCT (committed_at AT TIME ZONE 'America/New_York')::date AS report_day FROM paper_ledger WHERE committed_at >= now() - interval '30 days'
  UNION SELECT DISTINCT (filled_at AT TIME ZONE 'America/New_York')::date FROM paper_fills WHERE filled_at >= now() - interval '30 days'
  UNION SELECT DISTINCT (decided_at AT TIME ZONE 'America/New_York')::date FROM paper_orders WHERE decided_at >= now() - interval '30 days'
  UNION SELECT DISTINCT (settled_at AT TIME ZONE 'America/New_York')::date FROM paper_settlements WHERE settled_at >= now() - interval '30 days'),
r AS (SELECT report_day, bool_or(final) AS has_final FROM paper_audrey_reports GROUP BY 1)
SELECT count(*) AS days_with_activity, count(r.report_day) AS days_with_report,
       round(count(r.report_day)::numeric / greatest(count(*), 1), 3) AS coverage_ratio_actual, 1.0 AS coverage_ratio_expected,
       count(*) FILTER (WHERE act.report_day < (now() AT TIME ZONE 'America/New_York')::date) AS completed_days,
       count(*) FILTER (WHERE act.report_day < (now() AT TIME ZONE 'America/New_York')::date AND r.has_final) AS completed_days_with_final
  FROM act LEFT JOIN r USING (report_day);

\echo E1c AUDREY report versions and findings in the two windows (AFTER first)
SELECT 'AFTER' AS win, count(*) AS report_versions, count(*) FILTER (WHERE reconciles) AS reconciling, count(*) FILTER (WHERE final) AS finals, to_char(max(generated_at), 'MM-DD HH24:MI:SS') AS newest
  FROM paper_audrey_reports WHERE generated_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
UNION ALL
SELECT 'BEFORE', count(*), count(*) FILTER (WHERE reconciles), count(*) FILTER (WHERE final), to_char(max(generated_at), 'MM-DD HH24:MI:SS')
  FROM paper_audrey_reports WHERE generated_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' - interval '60 minutes' AND generated_at < TIMESTAMPTZ '2026-10-10 18:00:00+00';
SELECT CASE WHEN found_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' THEN 'AFTER' ELSE 'BEFORE' END AS win, kind, severity, count(*) AS findings, to_char(max(found_at), 'MM-DD HH24:MI:SS') AS newest
  FROM paper_audrey_findings WHERE found_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' - interval '60 minutes'
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT 30;

\echo E2 AUDREY reconciliation recomputed now, per account (the report's checks on the whole ledger)
WITH l AS (
  SELECT account_id, seq, kind, cash_delta_usd, reserved_delta_usd, cash_after_usd, reserved_after_usd, fill_id,
         lag(cash_after_usd) OVER (PARTITION BY account_id ORDER BY seq) AS prev_cash,
         lag(reserved_after_usd) OVER (PARTITION BY account_id ORDER BY seq) AS prev_res
    FROM paper_ledger),
acct AS (SELECT DISTINCT account_id FROM paper_ledger)
SELECT a.account_id,
       (SELECT count(*) FROM l WHERE l.account_id = a.account_id) AS ledger_rows,
       (SELECT round(sum(cash_delta_usd), 6) FROM l WHERE l.account_id = a.account_id) AS cash_sum,
       (SELECT cash_after_usd FROM l WHERE l.account_id = a.account_id ORDER BY seq DESC LIMIT 1) AS last_cash_after,
       abs((SELECT sum(cash_delta_usd) FROM l WHERE l.account_id = a.account_id) - (SELECT cash_after_usd FROM l WHERE l.account_id = a.account_id ORDER BY seq DESC LIMIT 1)) < 0.01 AS cash_matches,
       (SELECT count(*) FROM l WHERE l.account_id = a.account_id AND prev_cash IS NOT NULL
                                 AND (abs(cash_after_usd - (prev_cash + cash_delta_usd)) > 1e-6 OR abs(reserved_after_usd - (prev_res + reserved_delta_usd)) > 1e-6)) = 0 AS running_balances_consistent,
       (SELECT count(*) FROM paper_fills f WHERE f.account_id = a.account_id
           AND NOT EXISTS (SELECT 1 FROM paper_ledger x WHERE x.fill_id = f.fill_id AND x.kind IN ('FILL', 'SALE'))) AS fills_without_ledger_entry,
       (SELECT count(*) FROM l WHERE l.account_id = a.account_id AND kind IN ('FILL', 'SALE')
           AND NOT EXISTS (SELECT 1 FROM paper_fills f WHERE f.fill_id = l.fill_id)) AS ledger_fill_entries_without_fill,
       (SELECT round(coalesce(sum(payout_usd), 0), 6) FROM (SELECT DISTINCT ON (position_key, settlement_event_key) payout_usd FROM paper_settlements s
                                                              WHERE s.account_id = a.account_id ORDER BY position_key, settlement_event_key, version DESC) x) AS latest_settlement_payouts,
       (SELECT round(coalesce(sum(cash_delta_usd), 0), 6) FROM l WHERE l.account_id = a.account_id AND kind IN ('SETTLEMENT', 'CORRECTION')) AS ledger_settlement_and_corrections,
       abs((SELECT coalesce(sum(payout_usd), 0) FROM (SELECT DISTINCT ON (position_key, settlement_event_key) payout_usd FROM paper_settlements s
                                                        WHERE s.account_id = a.account_id ORDER BY position_key, settlement_event_key, version DESC) x)
           - (SELECT coalesce(sum(cash_delta_usd), 0) FROM l WHERE l.account_id = a.account_id AND kind IN ('SETTLEMENT', 'CORRECTION'))) < 0.01 AS settlements_match,
       (SELECT round(-sum(cash_delta_usd), 6) FROM l WHERE l.account_id = a.account_id AND kind = 'FILL') AS ledger_fill_debits,
       (SELECT round(sum(gross_usd + fee_usd), 6) FROM paper_fills f WHERE f.account_id = a.account_id AND direction = 'BUY') AS fills_buy_cost,
       (SELECT round(sum(cash_delta_usd), 6) FROM l WHERE l.account_id = a.account_id AND kind = 'SALE') AS ledger_sale_credits,
       (SELECT round(sum(gross_usd - fee_usd), 6) FROM paper_fills f WHERE f.account_id = a.account_id AND direction = 'SELL') AS fills_sale_proceeds
  FROM acct a ORDER BY 1;

\echo E3a XAVIER: reviews done in the two windows (AFTER first): reviews, groups, by trigger, passes that wrote reviews
SELECT CASE WHEN reviewed_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' THEN 'AFTER' ELSE 'BEFORE' END AS win, count(*) AS reviews, count(DISTINCT group_id) AS groups,
       count(*) FILTER (WHERE trigger = 'FIRST_FILL') AS first_fill, count(*) FILTER (WHERE trigger = 'FILL_EVENT') AS fill_event,
       count(*) FILTER (WHERE trigger = 'MARKET_EVENT') AS market_event, count(*) FILTER (WHERE trigger = 'SCHEDULED_BACKSTOP') AS backstop,
       count(*) FILTER (WHERE recommendation IS NOT NULL) AS with_recommendation, count(*) FILTER (WHERE refusal IS NOT NULL) AS with_refusal,
       to_char(min(reviewed_at), 'MM-DD HH24:MI:SS') AS first_at, to_char(max(reviewed_at), 'MM-DD HH24:MI:SS') AS last_at,
       round((count(*)::numeric / greatest(count(DISTINCT group_id), 1) / greatest(extract(epoch FROM (max(reviewed_at) - min(reviewed_at))) / 60.0, 1))::numeric, 2) AS reviews_per_group_per_min
  FROM paper_xavier_reviews
 WHERE reviewed_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' - interval '60 minutes'
 GROUP BY 1 ORDER BY 1;

\echo E3b XAVIER: every canonically open position now: handed off, reviews in each window, last review age, never reviewed
WITH c AS (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                       AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                       AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
),
rv AS (
  SELECT group_id, count(*) FILTER (WHERE reviewed_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00') AS after_reviews,
         count(*) FILTER (WHERE reviewed_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' - interval '60 minutes' AND reviewed_at < TIMESTAMPTZ '2026-10-10 18:00:00+00') AS before_reviews,
         count(*) AS reviews_ever, max(reviewed_at) AS last_review_at
    FROM paper_xavier_reviews WHERE group_id IN (SELECT group_id FROM c) GROUP BY 1)
SELECT c.account_id, left(c.group_id, 28) AS group_id, c.us_market_slug, c.holding_side, round(c.open_qty, 4) AS open_qty,
       (h.group_id IS NOT NULL) AS handed_off, to_char(h.first_fill_at, 'MM-DD HH24:MI') AS first_fill_at,
       coalesce(rv.after_reviews, 0) AS after_reviews, coalesce(rv.before_reviews, 0) AS before_reviews, coalesce(rv.reviews_ever, 0) AS reviews_ever,
       round(extract(epoch FROM now() - rv.last_review_at)::numeric, 0) AS last_review_age_s
  FROM c LEFT JOIN paper_handoffs h ON h.group_id = c.group_id LEFT JOIN rv ON rv.group_id = c.group_id
 ORDER BY coalesce(rv.after_reviews, 0), c.us_market_slug LIMIT 60;

\echo E3c XAVIER owed vs done: open groups, handed off, reviewed in AFTER / BEFORE, never reviewed, owed estimate (open handed groups x window minutes)
WITH c AS (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                       AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                       AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
),
g AS (SELECT DISTINCT account_id, group_id FROM c),
rv AS (
  SELECT group_id, count(*) FILTER (WHERE reviewed_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00') AS after_reviews,
         count(*) FILTER (WHERE reviewed_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' - interval '60 minutes' AND reviewed_at < TIMESTAMPTZ '2026-10-10 18:00:00+00') AS before_reviews,
         count(*) AS reviews_ever
    FROM paper_xavier_reviews WHERE group_id IN (SELECT group_id FROM g) GROUP BY 1)
SELECT g.account_id, count(*) AS open_groups, count(h.group_id) AS handed_off, count(*) - count(h.group_id) AS open_not_handed_off,
       count(*) FILTER (WHERE h.group_id IS NOT NULL AND coalesce(rv.after_reviews, 0) = 0) AS open_handed_without_review_in_AFTER,
       count(*) FILTER (WHERE h.group_id IS NOT NULL AND coalesce(rv.before_reviews, 0) = 0) AS open_handed_without_review_in_BEFORE,
       count(*) FILTER (WHERE coalesce(rv.reviews_ever, 0) = 0) AS open_without_any_review,
       coalesce(sum(rv.after_reviews), 0) AS reviews_done_AFTER, coalesce(sum(rv.before_reviews), 0) AS reviews_done_BEFORE,
       count(h.group_id) * floor(extract(epoch FROM now() - TIMESTAMPTZ '2026-10-10 18:00:00+00') / 60.0) AS reviews_owed_AFTER_est,
       count(h.group_id) * 60 AS reviews_owed_BEFORE_est
  FROM g LEFT JOIN paper_handoffs h ON h.group_id = g.group_id LEFT JOIN rv ON rv.group_id = g.group_id
 GROUP BY 1 ORDER BY 1;

\echo E4a ALLIE: canonical open positions (CANONICAL_OPEN_POSITIONS_SQL verbatim) per account
SELECT account_id, count(*) AS open_positions, count(DISTINCT group_id) AS open_groups, round(sum(open_qty), 6) AS open_qty FROM (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                       AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                       AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
) q GROUP BY 1 ORDER BY 1;

\echo E4b ALLIE: the exposure rows (OPEN_EXPOSURE_ROWS_SQL, account NULL = every account) per account: positions, open qty, exposure usd
SELECT account_id, count(*) AS open_positions, round(sum(open_qty), 6) AS open_qty, round(sum(exposure_usd), 4) AS exposure_usd FROM (
    SELECT c.account_id, c.group_id, c.us_market_slug, c.holding_side,
           c.fixture,
           c.open_qty * (c.buy_cost / nullif(c.bought, 0)) AS exposure_usd, c.open_qty
      FROM (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty,
           f.bought, f.fixture, f.buy_cost
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                       AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                       AS sold,
                   max(fixture) AS fixture,
                   sum(gross_usd + fee_usd) FILTER (WHERE direction = 'BUY')
                       AS buy_cost
              FROM paper_fills
             WHERE (NULL::text IS NULL OR account_id = NULL::text)
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  WHERE (NULL::text IS NULL OR starts_with(
                            position_key, 'paperpos:' || NULL::text || ':'))
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
) c
) e GROUP BY 1 ORDER BY 1;

\echo E4c ALLIE: positions where the canonical rule, the exposure statement (every account), the exposure statement scoped to the account, and the ledger arithmetic disagree (must be 0)
WITH canon AS (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                       AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                       AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
),
expo_all AS (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty,
           (f.bought - f.sold - coalesce(s.qty, 0)) * (f.buy_cost / nullif(f.bought, 0)) AS exposure_usd
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold,
                   max(fixture) AS fixture,
                   sum(gross_usd + fee_usd) FILTER (WHERE direction = 'BUY') AS buy_cost
              FROM paper_fills
             WHERE (NULL::text IS NULL OR account_id = NULL::text)
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  WHERE (NULL::text IS NULL OR starts_with(position_key, 'paperpos:' || NULL::text || ':'))
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9),
accounts AS (SELECT DISTINCT account_id FROM paper_fills),
expo_acct AS (
    SELECT e.* FROM accounts a CROSS JOIN LATERAL (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty,
           (f.bought - f.sold - coalesce(s.qty, 0)) * (f.buy_cost / nullif(f.bought, 0)) AS exposure_usd
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold,
                   max(fixture) AS fixture,
                   sum(gross_usd + fee_usd) FILTER (WHERE direction = 'BUY') AS buy_cost
              FROM paper_fills
             WHERE (a.account_id::text IS NULL OR account_id = a.account_id::text)
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  WHERE (a.account_id::text IS NULL OR starts_with(position_key, 'paperpos:' || a.account_id::text || ':'))
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9) e),
ledger_pos AS (
    SELECT lp.* FROM accounts a CROSS JOIN LATERAL (
    SELECT a.account_id, f.group_id, f.us_market_slug, f.holding_side, f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (
        SELECT group_id, us_market_slug, holding_side,
               coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
               coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
          FROM paper_fills WHERE account_id = a.account_id
         GROUP BY group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty FROM paper_settlements WHERE account_id = a.account_id
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || a.account_id || ':' || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9) lp),
keys AS (
  SELECT account_id, group_id, us_market_slug, holding_side FROM canon
  UNION SELECT account_id, group_id, us_market_slug, holding_side FROM expo_all
  UNION SELECT account_id, group_id, us_market_slug, holding_side FROM expo_acct
  UNION SELECT account_id, group_id, us_market_slug, holding_side FROM ledger_pos),
j AS (
  SELECT k.*, c.open_qty AS canon_qty, ea.open_qty AS expo_all_qty, ex.open_qty AS expo_acct_qty, lp.open_qty AS ledger_qty,
         ea.exposure_usd AS expo_all_usd, ex.exposure_usd AS expo_acct_usd
    FROM keys k
    LEFT JOIN canon c USING (account_id, group_id, us_market_slug, holding_side)
    LEFT JOIN expo_all ea USING (account_id, group_id, us_market_slug, holding_side)
    LEFT JOIN expo_acct ex USING (account_id, group_id, us_market_slug, holding_side)
    LEFT JOIN ledger_pos lp USING (account_id, group_id, us_market_slug, holding_side))
SELECT count(*) AS open_positions_union,
       count(*) FILTER (WHERE canon_qty IS NULL OR expo_all_qty IS NULL OR expo_acct_qty IS NULL OR ledger_qty IS NULL
                           OR abs(canon_qty - expo_all_qty) > 1e-9 OR abs(canon_qty - expo_acct_qty) > 1e-9 OR abs(canon_qty - ledger_qty) > 1e-9
                           OR abs(coalesce(expo_all_usd, 0) - coalesce(expo_acct_usd, 0)) > 1e-6) AS positions_disagreeing,
       round(coalesce(sum(expo_all_usd), 0), 4) AS book_exposure_usd_all, round(coalesce(sum(expo_acct_usd), 0), 4) AS book_exposure_usd_per_account,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM j;

\echo E4d ALLIE as recorded at decision time: MEASURED components in the two windows (AFTER first) and their book / fixture inputs
SELECT CASE WHEN created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' THEN 'AFTER' ELSE 'BEFORE' END AS win, allie->>'status' AS status, count(*) AS intents,
       count(DISTINCT allie->'correlation_concentration'->>'book_open_usd') AS distinct_book_open_usd,
       min(allie->'correlation_concentration'->>'book_open_usd') AS min_book_open_usd, max(allie->'correlation_concentration'->>'book_open_usd') AS max_book_open_usd,
       max(allie->'correlation_concentration'->>'fixture_open_groups') AS max_fixture_open_groups,
       to_char(max(created_at), 'MM-DD HH24:MI:SS') AS newest
  FROM canonical_decision_intents
 WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' - interval '60 minutes'
 GROUP BY 1, 2 ORDER BY 1, 2;
