-- READ-ONLY. THE PAIR COLLECTOR'S PER-CONTRACT ACCOUNT, AND THE NINE
-- CALIBRATION FIXTURES WHOSE EARLIEST ROW NEVER JOINED.
--
--   E  calibration: for each fixture whose EARLIEST in-scope valuation is
--      unjoined while a later one is joined, both rows side by side -- slug,
--      buy_intent, ladder_side, what the join read -- so the reason the
--      earliest row was not joined is visible, not inferred.
--   A  collector (rows written by the instrumented build only): each
--      attempt's conclusion; each sibling by contract, stage, refusal and
--      category; key fields that differ; rotation across restarts; and a
--      bounded census of the catalogue window against what was attempted.
--
-- No balance, credential or account field is selected.

\echo '== E1 · earliest unjoined row vs first joined row, per fixture =='
WITH s AS (
  SELECT id, event_key, observed_at, us_market_slug, buy_intent, ladder_side,
         probability, outcome_known, outcome_basis, outcome_side_map,
         settlement_read, settlement_read_at, refusals, record_purpose
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND decided_at >= now() - interval '90 days'
     AND version = 'PINNACLE_DEVIG_V1' AND devig_method = 'power'
     AND sport_family IN ('baseball', 'soccer') AND market = 'h2h'
     AND coalesce(event_key, '') <> ''),
fa AS (SELECT DISTINCT ON (event_key) * FROM s ORDER BY event_key, observed_at, id),
fj AS (SELECT DISTINCT ON (event_key) * FROM s WHERE outcome_known
        ORDER BY event_key, observed_at, id)
SELECT fa.event_key,
       fa.id AS first_id, fa.us_market_slug AS first_slug,
       fa.buy_intent AS first_buy_intent, fa.ladder_side AS first_ladder_side,
       fa.probability IS NOT NULL AS first_priced,
       fa.outcome_side_map AS first_side_map,
       fa.settlement_read AS first_settlement_read,
       fa.settlement_read_at AS first_read_at,
       fa.outcome_basis AS first_basis,
       fj.id AS joined_id, fj.us_market_slug AS joined_slug,
       fj.buy_intent AS joined_buy_intent, fj.ladder_side AS joined_ladder_side,
       fj.outcome_basis AS joined_basis
  FROM fa JOIN fj USING (event_key)
 WHERE NOT fa.outcome_known
 ORDER BY fa.id;

\echo '== E2 · every unjoined in-scope row: how the join last classified it =='
SELECT coalesce(outcome_side_map, '(never read)') AS side_map,
       coalesce(buy_intent, '(null)') AS buy_intent,
       coalesce(ladder_side, '(null)') AS ladder_side,
       settlement_read IS NOT NULL AS venue_answered,
       count(*) AS rows, count(DISTINCT event_key) AS fixtures,
       min(id) AS first_id
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at >= now() - interval '90 days'
   AND version = 'PINNACLE_DEVIG_V1' AND devig_method = 'power'
   AND sport_family IN ('baseball', 'soccer') AND market = 'h2h'
   AND NOT outcome_known
 GROUP BY 1, 2, 3, 4 ORDER BY rows DESC;

\echo '== A1 · attempts by conclusion (instrumented rows only) =='
SELECT detail->>'conclusion' AS conclusion, outcome,
       count(*) AS attempts, count(DISTINCT fixture) AS fixtures,
       sum(CASE WHEN (detail->>'budget_limited')::boolean THEN 1 ELSE 0 END)
         AS budget_limited,
       min(attempted_at) AS first, max(attempted_at) AS last
  FROM bettor_pair_observation_attempts
 WHERE detail ? 'conclusion'
 GROUP BY 1, 2 ORDER BY attempts DESC;

\echo '== A2 · each attempt: held contract, sibling totals, conclusion =='
SELECT attempt_id, attempted_at, candidate_source, fixture, us_market_slug,
       side, detail->'held'->>'sports_type' AS held_type,
       detail->'held'->'grading_key' AS held_key,
       (detail->>'fixture_candidate_pairs')::int AS fixture_pairs,
       (detail->>'siblings_total')::int AS siblings,
       detail->>'siblings_truncated_at_limit' AS truncated,
       detail->'sibling_categories' AS categories,
       detail->>'conclusion' AS conclusion
  FROM bettor_pair_observation_attempts
 WHERE detail ? 'conclusion'
 ORDER BY attempt_id DESC LIMIT 60;

\echo '== A3 · siblings by stage x category x refusal x contract type =='
WITH sib AS (
  SELECT a.attempt_id, a.fixture, e
    FROM bettor_pair_observation_attempts a,
         jsonb_array_elements(CASE WHEN jsonb_typeof(a.detail->'siblings') = 'array'
                                   THEN a.detail->'siblings' ELSE '[]'::jsonb END) e
   WHERE a.detail ? 'conclusion')
SELECT e->>'stage' AS stage, e->>'category' AS category,
       e->>'refusal' AS refusal,
       regexp_replace(coalesce(e->>'sports_type', ''), '^[a-z]+_', '')
         AS contract_type,
       coalesce(e->>'search_rank', '?') AS search_rank,
       count(*) AS siblings, count(DISTINCT fixture) AS fixtures,
       count(DISTINCT e->>'market_slug') AS contracts,
       min(e->>'market_slug') AS example
  FROM sib GROUP BY 1, 2, 3, 4, 5 ORDER BY siblings DESC LIMIT 80;

\echo '== A4 · discovery rejections: which grading-key fields differ =='
WITH sib AS (
  SELECT a.fixture, e
    FROM bettor_pair_observation_attempts a,
         jsonb_array_elements(CASE WHEN jsonb_typeof(a.detail->'siblings') = 'array'
                                   THEN a.detail->'siblings' ELSE '[]'::jsonb END) e
   WHERE a.detail ? 'conclusion' AND e->>'stage' = 'DISCOVERY')
SELECT coalesce((e->'key_differs_on')::text, '(key matched)') AS differs_on,
       e->>'refusal' AS refusal,
       regexp_replace(coalesce(e->>'sports_type', ''), '^[a-z]+_', '')
         AS contract_type,
       count(*) AS siblings, count(DISTINCT fixture) AS fixtures,
       min(e->>'market_slug') AS example, min((e->'grading_key')::text) AS example_key,
       min((e->'missing_facts')::text) AS example_missing
  FROM sib GROUP BY 1, 2, 3 ORDER BY siblings DESC LIMIT 40;

\echo '== A5 · rotation: attempts per fixture, before and after the instrumented build =='
SELECT fixture, count(*) AS attempts,
       count(*) FILTER (WHERE detail ? 'conclusion') AS instrumented_attempts,
       count(DISTINCT pass_id) AS passes,
       min(attempted_at) AS first, max(attempted_at) AS last,
       string_agg(DISTINCT outcome, ',') AS outcomes
  FROM bettor_pair_observation_attempts
 GROUP BY fixture ORDER BY attempts DESC, fixture LIMIT 40;

\echo '== A6 · bounded coverage census: catalogue window vs attempted =='
WITH win AS (
  SELECT lower(event_slug) AS fixture, market_slug, sports_type
    FROM us_premap
   WHERE game_start > now() + interval '600 seconds'
     AND game_start <= now() + interval '96 hours'
     AND updated_at > now() - interval '3900 seconds'
     AND event_slug IS NOT NULL AND market_slug IS NOT NULL
     AND split_part(coalesce(sports_type, ''), '_', 1) IN ('baseball', 'soccer')
     AND sports_type ~ '(_game_first_half_total_points|_game_second_half_total_points|_team_first_half_spread|_team_second_half_spread|_team_full_game_spread|_team_first_half_winner|_team_full_game_winner|_team_full_time_winner|_game_total_points|_match_winner|_fight_winner)$'),
fx AS (SELECT fixture, count(DISTINCT market_slug) AS graded_contracts
         FROM win GROUP BY 1),
att AS (SELECT lower(fixture) AS fixture, max(attempted_at) AS last,
               (array_agg(detail->>'conclusion' ORDER BY attempted_at DESC))[1]
                 AS last_conclusion
          FROM bettor_pair_observation_attempts GROUP BY 1)
SELECT CASE WHEN fx.graded_contracts < 2 THEN 'ONE_GRADED_CONTRACT'
            ELSE 'TWO_OR_MORE' END AS shape,
       coalesce(att.last_conclusion,
                CASE WHEN att.fixture IS NULL THEN '(not attempted)'
                     ELSE '(attempted before instrumentation)' END) AS last_conclusion,
       count(*) AS fixtures, sum(fx.graded_contracts) AS graded_contracts,
       min(fx.fixture) AS example
  FROM fx LEFT JOIN att USING (fixture)
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== A7 · observations recorded (the pairs actually admitted) =='
SELECT count(*) AS observations, count(DISTINCT fixture) AS fixtures,
       min(observed_at) AS first, max(observed_at) AS last
  FROM bettor_pair_observations;
