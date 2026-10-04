-- R30C HISTORICAL REPLAY: POINT-IN-TIME AVAILABILITY OF ITS INPUTS (SELECT only).
-- Branch claude/r30c-replay, sportsassets/replay/pit.py: the replay reads a row
-- only if it was recorded at or before the decision clock
--   clock = greatest(decided_at, recorded_at) of the paper decision.
-- This measures, for the last 7 days of INVESTMENT-strategy decisions, how
-- many of the replay's inputs existed BY THAT CLOCK versus at all, so the
-- replay's UNAVAILABLE components can be stated from production evidence:
--   valuation row (external_valuations.decided_at is its insert stamp),
--   event start: us_premap (rewritten in place -> visible only if its LAST
--     write is <= the clock) or an earlier xavier_entry_theses row,
--   Eddie's recorded estimate, the recorded canonical intent,
--   Karen's first record, the paper ledger's first row, settlement-lag history.

\echo === read time
SELECT now() AS read_at, now() - interval '7 days' AS window_start;

\echo === per strategy and verdict: inputs recorded by the decision clock vs at all
WITH d AS (
    SELECT decision_id, strategy, verdict, us_market_slug, valuation_id,
           greatest(decided_at, recorded_at) AS clk
      FROM paper_decisions
     WHERE decided_at >= now() - interval '7 days'
       AND strategy IN ('PINNACLE_COMPLETED_GAME_PAPER', 'DEREK_ENTRY_POLICY_V2')
     ORDER BY decided_at DESC
     LIMIT 50000
), slugs AS (
    SELECT DISTINCT us_market_slug FROM d WHERE us_market_slug IS NOT NULL
), pm AS (
    SELECT p.market_slug, min(p.updated_at) AS first_write, max(p.updated_at) AS last_write
      FROM us_premap p JOIN slugs s ON s.us_market_slug = p.market_slug
     WHERE p.game_start IS NOT NULL
     GROUP BY p.market_slug
), th AS (
    SELECT t.us_market_slug, min(t.recorded_at) AS first_rec
      FROM xavier_entry_theses t JOIN slugs s ON s.us_market_slug = t.us_market_slug
     WHERE t.event_start_at IS NOT NULL
     GROUP BY t.us_market_slug
), ed AS (
    SELECT e.decision_id, min(e.created_at) AS first_rec
      FROM eddie_execution_estimates e JOIN d ON d.decision_id = e.decision_id
     GROUP BY e.decision_id
)
SELECT d.strategy, d.verdict, count(*) AS decisions,
       count(*) FILTER (WHERE d.valuation_id IS NOT NULL) AS with_valuation_id,
       count(*) FILTER (WHERE v.id IS NOT NULL) AS valuation_row_exists,
       count(*) FILTER (WHERE v.decided_at <= d.clk) AS valuation_recorded_by_clock,
       count(*) FILTER (WHERE pm.market_slug IS NOT NULL) AS premap_row_exists,
       count(*) FILTER (WHERE pm.first_write <= d.clk) AS premap_some_row_written_by_clock,
       count(*) FILTER (WHERE th.first_rec <= d.clk) AS thesis_event_start_by_clock,
       count(*) FILTER (WHERE pm.first_write <= d.clk OR th.first_rec <= d.clk) AS event_start_by_clock_any_source,
       count(*) FILTER (WHERE ed.decision_id IS NOT NULL) AS eddie_estimate_exists,
       count(*) FILTER (WHERE ed.first_rec <= d.clk) AS eddie_estimate_by_clock,
       count(*) FILTER (WHERE c.recorded_at <= d.clk) AS canonical_intent_by_clock
  FROM d
  LEFT JOIN external_valuations v ON v.id = d.valuation_id
  LEFT JOIN pm ON pm.market_slug = d.us_market_slug
  LEFT JOIN th ON th.us_market_slug = d.us_market_slug
  LEFT JOIN ed ON ed.decision_id = d.decision_id
  LEFT JOIN canonical_decision_intents c ON c.decision_id = d.decision_id
 GROUP BY d.strategy, d.verdict
 ORDER BY d.strategy, d.verdict;

\echo === us_premap: how often a row is rewritten after it is first written (the in-place upsert)
SELECT count(*) AS rows,
       count(DISTINCT market_slug) AS slugs,
       min(updated_at) AS oldest_last_write,
       max(updated_at) AS newest_last_write,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM now() - updated_at)) AS median_age_of_last_write_s
  FROM us_premap
 WHERE game_start IS NOT NULL;

\echo === first records: Karen, the paper ledger, canonical intents, management intents
SELECT (SELECT min(created_at) FROM karen_challenges) AS karen_first_record,
       (SELECT count(*) FROM karen_challenges) AS karen_records,
       (SELECT min(committed_at) FROM paper_ledger WHERE account_id = 'paper_acct_main') AS ledger_first_row,
       (SELECT min(recorded_at) FROM canonical_decision_intents) AS canonical_intent_first,
       (SELECT count(*) FROM canonical_decision_intents) AS canonical_intents,
       (SELECT min(recorded_at) FROM canonical_management_intents) AS management_intent_first,
       (SELECT count(*) FROM canonical_management_intents) AS management_intents;

\echo === settlement-lag history for Allie / the tape hurdle: first ordinary settlement per market, 60 days
WITH s AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug, settled_at, recorded_at
      FROM paper_settlements
     WHERE outcome IN ('WON', 'LOST') AND settled_at >= now() - interval '60 days'
     ORDER BY us_market_slug, settled_at
), pm AS (
    SELECT p.market_slug, min(p.updated_at) AS first_write
      FROM us_premap p JOIN s ON s.us_market_slug = p.market_slug
     WHERE p.game_start IS NOT NULL GROUP BY p.market_slug
), th AS (
    SELECT t.us_market_slug, min(t.recorded_at) AS first_rec
      FROM xavier_entry_theses t JOIN s ON s.us_market_slug = t.us_market_slug
     WHERE t.event_start_at IS NOT NULL GROUP BY t.us_market_slug
)
SELECT count(*) AS settled_markets,
       count(*) FILTER (WHERE pm.first_write <= s.recorded_at) AS premap_start_written_before_settlement_recorded,
       count(*) FILTER (WHERE th.first_rec <= s.recorded_at) AS thesis_start_before_settlement_recorded,
       count(*) FILTER (WHERE pm.first_write <= s.recorded_at OR th.first_rec <= s.recorded_at) AS usable_lag_samples
  FROM s
  LEFT JOIN pm ON pm.market_slug = s.us_market_slug
  LEFT JOIN th ON th.us_market_slug = s.us_market_slug;
