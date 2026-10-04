-- P0 INCIDENT (coverage -> trade starvation), FULL FUNNEL RECEIPT, PART C (read-only).
-- Precise attribution behind parts A/B: why provider events have no Pinnacle price
-- (time to start), whose clock made a quote stale on arrival, the INVESTMENT lane
-- (completed-game V3 + Derek) per opportunity stage by stage, the existing receipt's
-- ENTER split by sleeve, order expiry reasons, actual-side refusals, the venue's
-- full-game winner slate by family, reactive attempts and the lost-opportunity ledger.
\echo '== C1 . NO_PINNACLE_ON_EVENT events 24 h by sport: hours from the cycle to the event start at the LATEST such row =='
WITH r AS (
    SELECT DISTINCT ON (sport_key, provider_event_id) sport_key, provider_event_id, cycle_at,
           CASE WHEN commence_time ~ '^\d{4}-\d{2}-\d{2}T' THEN commence_time::timestamptz END AS ct
      FROM ext_candidate_outcomes
     WHERE cycle_at > now() - interval '24 hours' AND first_refusal = 'NO_PINNACLE_ON_EVENT'
     ORDER BY sport_key, provider_event_id, cycle_at DESC),
ever AS (
    SELECT sport_key, provider_event_id,
           bool_or(coalesce(first_refusal,'') <> 'NO_PINNACLE_ON_EVENT') AS priced_some_cycle
      FROM ext_candidate_outcomes WHERE cycle_at > now() - interval '24 hours'
     GROUP BY 1, 2)
SELECT r.sport_key,
       CASE WHEN r.ct IS NULL THEN 'START_UNPARSEABLE'
            WHEN r.ct <= r.cycle_at THEN 'a_STARTED_IN_PLAY'
            WHEN r.ct <= r.cycle_at + interval '6 hours' THEN 'b_0_6H'
            WHEN r.ct <= r.cycle_at + interval '24 hours' THEN 'c_6_24H'
            WHEN r.ct <= r.cycle_at + interval '72 hours' THEN 'd_24_72H'
            ELSE 'e_OVER_72H' END AS start_bucket,
       count(*) AS events, count(*) FILTER (WHERE ever.priced_some_cycle) AS priced_in_another_cycle
  FROM r JOIN ever USING (sport_key, provider_event_id)
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== C2 . QUOTE_STALE_ON_ARRIVAL rows 24 h by sport: provider lag vs our processing (seconds) =='
SELECT sport_key, count(*) AS rows, count(DISTINCT provider_event_id) AS events,
       count(*) FILTER (WHERE provider_lag_s > 30) AS provider_lag_over_30s,
       count(*) FILTER (WHERE provider_lag_s <= 30) AS provider_inside_30s_ours_pushed_over,
       count(*) FILTER (WHERE provider_lag_s IS NULL) AS lag_unmeasured,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_lag_s)::numeric, 1) AS lag_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY provider_lag_s)::numeric, 1) AS lag_p90,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY our_processing_s)::numeric, 1) AS ours_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY our_processing_s)::numeric, 1) AS ours_p90
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours' AND first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
 GROUP BY 1 ORDER BY 2 DESC;
SELECT sport_key, count(*) AS priced_rows,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_lag_s)::numeric, 1) AS lag_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY provider_lag_s)::numeric, 1) AS lag_p90,
       count(*) FILTER (WHERE provider_lag_s > 30) AS lag_over_30s
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours' AND provider_lag_s IS NOT NULL
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== C3 . INVESTMENT LANE (CG V3 + Derek) 24 h per strategy / league: unique opportunities reaching each stage on ANY evaluation =='
WITH d AS (
    SELECT strategy, split_part(us_market_slug,'-',2) AS league, us_market_slug, holding_side, verdict, refusals,
           economics, decision_id,
           NOT (refusals && ARRAY['FIXTURE_IDENTITY_NOT_ESTABLISHED','REAL_EVENT_NOT_ESTABLISHED',
                'PAYOUT_OUTCOME_MATCH_NOT_ESTABLISHED','MARKET_OR_LINE_NOT_A_MONEYLINE_MATCH',
                'GRADING_PERIOD_NOT_FULL_GAME','PINNAPI_PRIMARY_NO_EXACT_FIXTURE']::text[]) AS id_ok,
           NOT (refusals && ARRAY['SETTLEMENT_NOT_SUPPORTED','NO_COMPLETED_GAME_TERMS_FOR_THIS_SPORT',
                'VENUE_RULES_TEXT_NOT_RECORDED_ON_THE_VALUATION_ROW','ORDINARY_GRADING_PERIOD_NOT_ESTABLISHED',
                'ORDINARY_GRADING_PERIOD_MISMATCH']::text[]) AS settle_ok,
           NOT (refusals && ARRAY['NO_QUALIFIED_PINNACLE_PROBABILITY','PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE',
                'OUTCOME_DEPTH_BELOW_FLOOR','PINNAPI_PRIMARY_INPUT_CHANGED','PINNAPI_PRIMARY_CLOCK_INVALID']::text[]) AS prob_ok,
           NOT (refusals && ARRAY['PROBABILITY_EVIDENCE_STALE','PROBABILITY_EVIDENCE_FRESHNESS_UNKNOWN',
                'FEED_QUOTE_OLDER_THAN_LIMIT','FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE','FEED_SOCKET_CLOSED']::text[]) AS fresh_ok,
           NOT (refusals && ARRAY['THE_PAPER_BOOK_OBSERVATION_IS_NOT_CURRENT','BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE',
                'NO_ESTABLISHED_EXECUTABLE_DEPTH']::text[]) AS book_ok,
           NOT (refusals && ARRAY['NO_RESEARCH_MODEL_CANDIDATE_EXISTS','NO_APPROVED_INTERNAL_MODEL',
                'INTERNAL_MODEL_CANNOT_SCORE_THIS_CANDIDATE']::text[]) AS model_ok,
           CASE WHEN strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
                THEN CASE WHEN economics->>'best_level_edge_pp' ~ '^-?[0-9.]+(e-?[0-9]+)?$'
                          THEN (economics->>'best_level_edge_pp')::float8 END
                ELSE CASE WHEN economics->'pinnacle'->>'gross_edge_pp' ~ '^-?[0-9.]+(e-?[0-9]+)?$'
                          THEN (economics->'pinnacle'->>'gross_edge_pp')::float8 END END AS edge_pp,
           CASE WHEN strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
                THEN (economics->>'best_level_edge_pp' ~ '^-?[0-9.]+(e-?[0-9]+)?$'
                      AND economics->>'threshold_edge_pp' ~ '^-?[0-9.]+(e-?[0-9]+)?$'
                      AND (economics->>'best_level_edge_pp')::float8 >= (economics->>'threshold_edge_pp')::float8)
                ELSE economics->'pinnacle'->>'clears_min_gross_edge' = 'true' END AS clears,
           CASE WHEN strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
                THEN economics->'acquisition'->>'net_ev_positive' = 'true'
                ELSE NOT (refusals && ARRAY['NET_EV_NOT_POSITIVE_AFTER_FEES','BELOW_MIN_NET_EV']::text[])
                     AND economics->'pinnacle'->>'clears_min_gross_edge' = 'true' END AS net_pos
      FROM paper_decisions
     WHERE decided_at > now() - interval '24 hours'
       AND strategy IN ('PINNACLE_COMPLETED_GAME_PAPER','DEREK_ENTRY_POLICY_V2') AND us_market_slug IS NOT NULL),
o AS (
    SELECT DISTINCT po.decision_id, po.order_id, (po.filled_qty > 0) AS filled
      FROM paper_orders po WHERE po.role = 'ENTRY' AND po.created_at > now() - interval '24 hours')
SELECT strategy, league,
       count(DISTINCT (us_market_slug, holding_side)) AS s0_decided,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE id_ok) AS s1_identity,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE id_ok AND settle_ok) AS s2_settlement,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE id_ok AND settle_ok AND prob_ok) AS s3_probability,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE id_ok AND settle_ok AND prob_ok AND fresh_ok) AS s4_fresh,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE id_ok AND settle_ok AND prob_ok AND fresh_ok AND book_ok) AS s5_book,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE id_ok AND settle_ok AND prob_ok AND fresh_ok AND book_ok AND edge_pp IS NOT NULL) AS s6_ev_evaluated,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE id_ok AND settle_ok AND prob_ok AND fresh_ok AND book_ok AND edge_pp > 0) AS s7_gross_edge_pos,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE id_ok AND settle_ok AND prob_ok AND fresh_ok AND book_ok AND clears) AS s8_clears_threshold,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE id_ok AND settle_ok AND prob_ok AND fresh_ok AND book_ok AND net_pos) AS s9_net_ev_pos,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE model_ok) AS model_ok_any,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE verdict = 'ENTER') AS s10_enter,
       count(DISTINCT (d.us_market_slug, d.holding_side)) FILTER (WHERE o.order_id IS NOT NULL) AS s11_ordered,
       count(DISTINCT (d.us_market_slug, d.holding_side)) FILTER (WHERE o.filled) AS s12_filled,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE edge_pp IS NOT NULL) AS ev_evaluated_ignoring_gates,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE edge_pp > 0) AS gross_pos_ignoring_gates
  FROM d LEFT JOIN o ON o.decision_id = d.decision_id
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== C3b . INVESTMENT LANE 24 h: best gross edge per opportunity (pp) distribution per strategy / league =='
WITH d AS (
    SELECT strategy, split_part(us_market_slug,'-',2) AS league, us_market_slug, holding_side,
           CASE WHEN strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
                THEN CASE WHEN economics->>'best_level_edge_pp' ~ '^-?[0-9.]+(e-?[0-9]+)?$'
                          THEN (economics->>'best_level_edge_pp')::float8 END
                ELSE CASE WHEN economics->'pinnacle'->>'gross_edge_pp' ~ '^-?[0-9.]+(e-?[0-9]+)?$'
                          THEN (economics->'pinnacle'->>'gross_edge_pp')::float8 END END AS edge_pp,
           economics->>'threshold_edge_pp' AS thr
      FROM paper_decisions
     WHERE decided_at > now() - interval '24 hours'
       AND strategy IN ('PINNACLE_COMPLETED_GAME_PAPER','DEREK_ENTRY_POLICY_V2') AND us_market_slug IS NOT NULL),
b AS (SELECT strategy, league, us_market_slug, holding_side, max(edge_pp) AS best, max(thr) AS thr FROM d GROUP BY 1,2,3,4)
SELECT strategy, league, count(*) AS opps, count(best) AS with_edge,
       count(*) FILTER (WHERE best > 0) AS best_pos, count(*) FILTER (WHERE best > 1) AS best_over_1pp,
       count(*) FILTER (WHERE best > 2) AS best_over_2pp, round(max(best)::numeric, 3) AS max_best_pp,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY best))::numeric, 3) AS median_best_pp, max(thr) AS threshold_pp
  FROM b GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== C4 . EXISTING RECEIPT ENTER split by sleeve, ET today (provider event of the valuation, as coverage_integrity counts it) =='
WITH m AS (
    SELECT DISTINCT ON (provider_event_id) provider_event_id, sport_key
      FROM ext_candidate_outcomes WHERE provider_event_id IS NOT NULL AND cycle_at > now() - interval '15 days'
     ORDER BY provider_event_id, cycle_at DESC),
d AS (
    SELECT coalesce(m.sport_key, 'UNATTRIBUTED') AS league, pd.strategy, coalesce(ev.event_key, pd.us_market_slug) AS ek,
           bool_or(pd.verdict = 'ENTER') AS entered
      FROM paper_decisions pd JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
     WHERE pd.decided_at >= date_trunc('day', now() AT TIME ZONE 'America/New_York') AT TIME ZONE 'America/New_York'
     GROUP BY 1, 2, 3)
SELECT league, strategy, count(*) AS decided_events, count(*) FILTER (WHERE entered) AS entered_events
  FROM d GROUP BY 1, 2 ORDER BY 1, 2;
WITH m AS (
    SELECT DISTINCT ON (provider_event_id) provider_event_id, sport_key
      FROM ext_candidate_outcomes WHERE provider_event_id IS NOT NULL AND cycle_at > now() - interval '15 days'
     ORDER BY provider_event_id, cycle_at DESC)
SELECT coalesce(m.sport_key, 'UNATTRIBUTED') AS league, ei.strategy, ei.actual_state,
       count(DISTINCT coalesce(ev.event_key, ei.us_market_slug)) AS intent_events,
       count(DISTINCT coalesce(ev.event_key, ei.us_market_slug)) FILTER (WHERE ei.actual_mirror_id IS NOT NULL) AS submitted_events
  FROM execution_intents ei LEFT JOIN external_valuations ev ON ev.id = ei.valuation_id
  LEFT JOIN m ON m.provider_event_id = ev.event_key
 WHERE ei.decided_at >= date_trunc('day', now() AT TIME ZONE 'America/New_York') AT TIME ZONE 'America/New_York'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== C5 . ENTRY order expiry reasons 24 h by strategy (one row per order) =='
SELECT strategy, state, coalesce(terminal_reason,'-') AS terminal_reason, count(*) AS orders,
       count(DISTINCT (us_market_slug, holding_side)) AS opportunities
  FROM paper_orders WHERE role = 'ENTRY' AND created_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC;

\echo '== C6 . EXECUTION INTENTS 24 h per league / strategy / refusal (actual side) =='
SELECT split_part(us_market_slug,'-',2) AS league, strategy, actual_state, coalesce(actual_refusal,'-') AS actual_refusal,
       count(*) AS intents, count(DISTINCT us_market_slug) AS markets
  FROM execution_intents WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC;

\echo '== C7 . VENUE live slate (start now-4h .. now+36h): FULL-GAME WINNER events by family and league token =='
SELECT split_part(coalesce(sports_type,'?'),'_',1) AS family,
       lower(split_part(coalesce(event_slug,''),'-',1)) AS token,
       count(DISTINCT event_slug) AS events, count(DISTINCT market_slug) AS markets,
       count(DISTINCT event_slug) FILTER (WHERE game_start > now()) AS events_not_started
  FROM us_premap
 WHERE game_start >= now() - interval '4 hours' AND game_start < now() + interval '36 hours'
   AND (sports_type LIKE '%full_game_winner' OR sports_type LIKE '%full_time_winner' OR sports_type LIKE '%match_winner')
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 80;
SELECT split_part(coalesce(sports_type,'?'),'_',1) AS family,
       count(DISTINCT event_slug) AS events, count(DISTINCT market_slug) AS markets
  FROM us_premap
 WHERE game_start >= now() - interval '4 hours' AND game_start < now() + interval '36 hours'
   AND (sports_type LIKE '%full_game_winner' OR sports_type LIKE '%full_time_winner' OR sports_type LIKE '%match_winner')
 GROUP BY 1 ORDER BY 2 DESC;
\echo '== C7b . VENUE 24 h back-window (events that started in the last 24 h): FULL-GAME WINNER events by family =='
SELECT split_part(coalesce(sports_type,'?'),'_',1) AS family,
       count(DISTINCT event_slug) AS events, count(DISTINCT market_slug) AS markets
  FROM us_premap
 WHERE game_start >= now() - interval '24 hours' AND game_start < now()
   AND (sports_type LIKE '%full_game_winner' OR sports_type LIKE '%full_time_winner' OR sports_type LIKE '%match_winner')
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== C8 . PINNAPI reactive attempts 24 h by state =='
SELECT state, count(*) AS attempts, count(DISTINCT event_id) AS events,
       left(max(detail->>'refusal'), 80) AS a_refusal, left(max(detail->>'why'), 80) AS a_why
  FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== C9 . LOST-OPPORTUNITY LEDGER 24 h by strategy / classification / refusal category =='
SELECT strategy, classification, coalesce(refusal_category,'-') AS refusal_category, coalesce(attribution_code,'-') AS attribution_code,
       count(*) AS rows, count(DISTINCT (us_market_slug, holding_side)) AS opportunities
  FROM lol_ledger WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 40;

\echo '== C10 . 7-DAY INVESTMENT trend per UTC day: unique opportunities EV-evaluated / gross-positive / clears / ENTER (CG and Derek) =='
WITH d AS (
    SELECT date_trunc('day', decided_at) AS day, strategy, us_market_slug, holding_side, verdict,
           CASE WHEN strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
                THEN CASE WHEN economics->>'best_level_edge_pp' ~ '^-?[0-9.]+(e-?[0-9]+)?$'
                          THEN (economics->>'best_level_edge_pp')::float8 END
                WHEN strategy = 'DEREK_ENTRY_POLICY_V2'
                THEN CASE WHEN economics->'pinnacle'->>'gross_edge_pp' ~ '^-?[0-9.]+(e-?[0-9]+)?$'
                          THEN (economics->'pinnacle'->>'gross_edge_pp')::float8 END
                ELSE CASE WHEN economics->>'best_level_edge_pp' ~ '^-?[0-9.]+(e-?[0-9]+)?$'
                          THEN (economics->>'best_level_edge_pp')::float8 END END AS edge_pp
      FROM paper_decisions WHERE decided_at > date_trunc('day', now()) - interval '7 days')
SELECT day, strategy, count(DISTINCT (us_market_slug, holding_side)) AS opps_decided,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE edge_pp IS NOT NULL) AS opps_ev_evaluated,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE edge_pp > 0) AS opps_gross_edge_pos,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE verdict = 'ENTER') AS opps_enter,
       count(DISTINCT split_part(us_market_slug,'-',2)) AS leagues
  FROM d GROUP BY 1, 2 ORDER BY 1, 2;
