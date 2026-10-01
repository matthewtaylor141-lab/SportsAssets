-- READ-ONLY. THE COMPLETE COLLECTION FUNNEL AND CADENCE, DISTINCT FIXTURES
-- APART FROM REPEATED OBSERVATIONS. Nothing here writes.
--
-- K0 the latest cycle's own funnel per provider/sport (provider events ->
--    identity -> Pinnacle h2h -> mapped to a venue contract -> open & fresh
--    -> written), and its refusals
-- K1 cycle cadence: valuation bursts (a gap > 120 s starts a new cycle)
--    over the last 6 h, with start, duration and rows per cycle
-- K2 since the 0fa0fd2 release (13:51:55Z): valuations vs distinct contracts
--    vs distinct fixtures (event_key), by sport and purpose
-- K3 the completed-game decision funnel by DISTINCT contract and by
--    observation: decided -> exact match -> fresh Pinnacle -> usable book ->
--    edge evaluated -> edge >= 5 pp -> EV > 0 -> ordered -> filled
-- K4 refusal breakdown (observations and distinct contracts)
-- K5 the edge distribution of every evaluated completed-game observation
-- K6 in-cycle hook failures (table from migration 187, when present)

\echo '== K0 · latest cycle funnel per provider/sport =='
SELECT s.value->>'at' AS cycle_at, f.key AS provider_sport,
       f.value->>'provider_events' AS provider_events,
       f.value->>'identity_resolved' AS identity_resolved,
       f.value->>'with_pinnacle_h2h' AS with_pinnacle_h2h,
       f.value->>'mapped_to_a_venue_contract' AS mapped,
       f.value->>'venue_markets_open_and_fresh' AS open_fresh,
       f.value->>'recorded_for_calibration_only' AS calibration_only,
       f.value->>'written' AS written,
       left((f.value->'refusals')::text, 300) AS refusals
  FROM ingestion_state s, jsonb_each(s.value->'funnel_by_provider_sport') f
 WHERE s.key = 'ext_pinnacle_last_cycle'
 ORDER BY (f.value->>'provider_events')::int DESC NULLS LAST;
SELECT s.value->>'at' AS cycle_at,
       left((s.value - 'funnel_by_provider_sport')::text, 1500) AS cycle_summary
  FROM ingestion_state s WHERE s.key = 'ext_pinnacle_last_cycle';

\echo '== K1 · cycle cadence (valuation bursts, last 6 h) =='
WITH v AS (
  SELECT decided_at,
         CASE WHEN decided_at - lag(decided_at) OVER (ORDER BY decided_at)
                   > interval '120 seconds' THEN 1 ELSE 0 END AS new_cycle
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND decided_at > now() - interval '6 hours'),
c AS (SELECT decided_at, sum(new_cycle) OVER (ORDER BY decided_at) AS cyc
        FROM v)
SELECT cyc, min(decided_at) AS first_row, max(decided_at) AS last_row,
       extract(epoch FROM max(decided_at) - min(decided_at))::int
         AS burst_s,
       extract(epoch FROM min(decided_at) - lag(min(decided_at))
               OVER (ORDER BY cyc))::int AS since_previous_s,
       count(*) AS rows
  FROM c GROUP BY cyc ORDER BY cyc;

\echo '== K2 · since 13:51:55Z: observations vs distinct contracts vs fixtures =='
SELECT sport_family, record_purpose,
       count(*) AS observations,
       count(DISTINCT us_market_slug) AS distinct_contracts,
       count(DISTINCT event_key) AS distinct_fixtures,
       count(*) FILTER (WHERE settlement_comparison->>'venue_rules_text'
                        IS NOT NULL) AS with_venue_text
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at > '2026-10-01 13:51:55+00'
 GROUP BY ROLLUP (1, 2) ORDER BY 1 NULLS LAST, 2 NULLS LAST;

\echo '== K3 · completed-game funnel, observations and distinct contracts =='
WITH c AS (
  SELECT d.us_market_slug AS slug, v.event_key,
         (d.policy_decision->'conditions'->0->>'passed')::boolean AS match_ok,
         (d.policy_decision->'conditions'->1->>'passed')::boolean AS fresh_ok,
         (d.policy_decision->'conditions'->2->>'passed')::boolean AS book_ok,
         (d.economics->>'best_level_edge_pp') IS NOT NULL AS edge_eval,
         (d.policy_decision->'conditions'->3->>'passed')::boolean AS edge_ok,
         (d.policy_decision->'conditions'->4->>'passed')::boolean AS ev_ok,
         EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id =
                 d.decision_id AND o.role = 'ENTRY') AS ordered,
         EXISTS (SELECT 1 FROM paper_orders o JOIN paper_fills f
                     ON f.order_id = o.order_id
                  WHERE o.decision_id = d.decision_id) AS filled
    FROM paper_decisions d JOIN external_valuations v ON v.id = d.valuation_id
   WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER')
SELECT 'observations' AS unit, count(*) AS decided,
       count(*) FILTER (WHERE match_ok) AS exact_match,
       count(*) FILTER (WHERE match_ok AND fresh_ok) AS fresh_pinnacle,
       count(*) FILTER (WHERE match_ok AND fresh_ok AND book_ok)
         AS usable_book,
       count(*) FILTER (WHERE edge_eval) AS edge_evaluated,
       count(*) FILTER (WHERE edge_ok) AS edge_5pp,
       count(*) FILTER (WHERE edge_ok AND ev_ok) AS ev_positive,
       count(*) FILTER (WHERE ordered) AS ordered,
       count(*) FILTER (WHERE filled) AS filled
  FROM c
UNION ALL
SELECT 'distinct_contracts', count(DISTINCT slug),
       count(DISTINCT slug) FILTER (WHERE match_ok),
       count(DISTINCT slug) FILTER (WHERE match_ok AND fresh_ok),
       count(DISTINCT slug) FILTER (WHERE match_ok AND fresh_ok AND book_ok),
       count(DISTINCT slug) FILTER (WHERE edge_eval),
       count(DISTINCT slug) FILTER (WHERE edge_ok),
       count(DISTINCT slug) FILTER (WHERE edge_ok AND ev_ok),
       count(DISTINCT slug) FILTER (WHERE ordered),
       count(DISTINCT slug) FILTER (WHERE filled)
  FROM c
UNION ALL
SELECT 'distinct_fixtures', count(DISTINCT event_key),
       count(DISTINCT event_key) FILTER (WHERE match_ok),
       count(DISTINCT event_key) FILTER (WHERE match_ok AND fresh_ok),
       count(DISTINCT event_key) FILTER (WHERE match_ok AND fresh_ok
                                         AND book_ok),
       count(DISTINCT event_key) FILTER (WHERE edge_eval),
       count(DISTINCT event_key) FILTER (WHERE edge_ok),
       count(DISTINCT event_key) FILTER (WHERE edge_ok AND ev_ok),
       count(DISTINCT event_key) FILTER (WHERE ordered),
       count(DISTINCT event_key) FILTER (WHERE filled)
  FROM c;

\echo '== K4 · completed-game refusals =='
SELECT coalesce(refusal, 'ENTER') AS first_refusal, count(*) AS observations,
       count(DISTINCT us_market_slug) AS distinct_contracts,
       max(decided_at) AS latest
  FROM paper_decisions WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== K5 · edge distribution of evaluated observations =='
SELECT width_bucket((economics->>'best_level_edge_pp')::float8,
                    -10, 10, 20) AS bucket,
       min((economics->>'best_level_edge_pp')::float8) AS min_pp,
       max((economics->>'best_level_edge_pp')::float8) AS max_pp,
       count(*) AS observations,
       count(DISTINCT us_market_slug) AS contracts
  FROM paper_decisions
 WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND economics->>'best_level_edge_pp' IS NOT NULL
 GROUP BY 1 ORDER BY 1;
SELECT us_market_slug, max((economics->>'best_level_edge_pp')::float8)
         AS best_edge_pp, count(*) AS observations, max(decided_at) AS latest
  FROM paper_decisions
 WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND economics->>'best_level_edge_pp' IS NOT NULL
 GROUP BY 1 ORDER BY 2 DESC LIMIT 12;

\echo '== K6 · in-cycle hook failures (when migration 187 is present) =='
SELECT to_regclass('paper_hook_failures') IS NOT NULL AS table_present;
