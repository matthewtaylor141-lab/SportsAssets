-- P0 INCIDENT (inc-edge), READ-ONLY: VALIDATE THE GROSS-EDGE INPUTS.
--
-- Owner: "preserve the threshold, but validate its inputs" -- the Pinnacle fair
-- probability, the vig removal, the venue executable price, the fee treatment
-- and the market equivalence behind every BELOW_MIN_GROSS_EDGE refusal.
--
-- Every paper strategy computes gross edge as
--     p (external_valuations.probability, de-vigged, oriented once)
--   - the acquisition price of the best level of the CONSUMED side of the
--     decision's own paper book observation (LONG: offers at px; SHORT: bids
--     at 1 - px), cent grid only (bettor_paper_simulator.levels_for).
-- This file reads the RECORDED inputs as the real writers persisted them and
-- recomputes what can be recomputed in SQL; the power de-vig is recomputed
-- offline from E7's raw odds. Window: the last 7 days. Every row dump is
-- bounded. No table is written.
\echo '== E0 · read instant =='
SELECT now() AS read_at, (SELECT max(version) FROM schema_migrations) AS schema_head;

\echo '== E1 · paper decisions (7 d): ENTER and the economic refusals, by strategy =='
SELECT strategy, policy_version, verdict, coalesce(refusal, '-') AS refusal,
       count(*) AS n, count(DISTINCT us_market_slug) AS markets,
       min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM paper_decisions
 WHERE decided_at > now() - interval '7 days'
   AND (verdict = 'ENTER' OR refusal IN (
        'BELOW_MIN_GROSS_EDGE', 'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT',
        'NET_EV_NOT_POSITIVE_AFTER_FEES', 'NO_SIZED_QUANTITY', 'FEES_NOT_ESTABLISHED'))
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 5 DESC;

\echo '== E2 · the same, by sport family and league token (slug part 2) =='
SELECT coalesce(v.sport_family, '?') AS sport, split_part(d.us_market_slug, '-', 2) AS league,
       d.strategy,
       count(*) FILTER (WHERE d.verdict = 'ENTER') AS enter,
       count(*) FILTER (WHERE d.refusal = 'BELOW_MIN_GROSS_EDGE') AS below_min,
       count(DISTINCT d.us_market_slug) FILTER (WHERE d.refusal = 'BELOW_MIN_GROSS_EDGE') AS below_min_markets,
       count(*) FILTER (WHERE d.refusal = 'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT') AS fees_consume,
       count(*) FILTER (WHERE d.refusal = 'NET_EV_NOT_POSITIVE_AFTER_FEES') AS net_ev_neg,
       count(*) FILTER (WHERE d.refusal = 'NO_SIZED_QUANTITY') AS no_qty
  FROM paper_decisions d
  LEFT JOIN external_valuations v ON v.id = d.valuation_id
 WHERE d.decided_at > now() - interval '7 days'
   AND (d.verdict = 'ENTER' OR d.refusal IN (
        'BELOW_MIN_GROSS_EDGE', 'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT',
        'NET_EV_NOT_POSITIVE_AFTER_FEES', 'NO_SIZED_QUANTITY'))
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== E3 · recorded gross edge == p - levels[0].price? (arithmetic consistency) =='
WITH d AS (
  SELECT d.strategy, d.refusal, d.verdict, d.p_pinnacle AS p,
         (d.policy_decision->>'gross_edge_pp')::float8 AS ge_pp,
         (d.economics->'levels'->0->>'price')::float8 AS lv0
    FROM paper_decisions d
   WHERE d.decided_at > now() - interval '7 days'
     AND (d.verdict = 'ENTER' OR d.refusal IN ('BELOW_MIN_GROSS_EDGE',
          'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT',
          'NET_EV_NOT_POSITIVE_AFTER_FEES', 'NO_SIZED_QUANTITY')))
SELECT strategy, count(*) AS n,
       count(*) FILTER (WHERE ge_pp IS NULL) AS no_gross_edge,
       count(*) FILTER (WHERE lv0 IS NULL) AS no_level0,
       count(*) FILTER (WHERE abs((p - lv0) * 100 - ge_pp) > 1e-6) AS mismatched,
       round(max(abs((p - lv0) * 100 - ge_pp))::numeric, 9) AS max_abs_diff_pp
  FROM d GROUP BY 1 ORDER BY 1;

\echo '== E4 · the decision book recomputed from the RAW observation (consumed side, cent grid) =='
WITH d AS (
  SELECT d.decision_id, d.strategy, d.refusal, d.verdict, d.holding_side,
         d.p_pinnacle AS p, d.book_obs_id,
         (d.policy_decision->>'threshold_edge_pp')::float8 AS thr_pp,
         (d.economics->'levels'->0->>'price')::float8 AS lv0,
         split_part(d.us_market_slug, '-', 2) AS league
    FROM paper_decisions d
   WHERE d.decided_at > now() - interval '7 days' AND d.book_obs_id IS NOT NULL
     AND (d.verdict = 'ENTER' OR d.refusal IN ('BELOW_MIN_GROSS_EDGE',
          'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT',
          'NET_EV_NOT_POSITIVE_AFTER_FEES', 'NO_SIZED_QUANTITY'))),
lv AS (
  SELECT d.*, o.error AS book_err, s.side, s.px, s.q
    FROM d JOIN paper_book_observations o ON o.obs_id = d.book_obs_id
    CROSS JOIN LATERAL (
      SELECT 'offers' AS side, e FROM jsonb_array_elements(
             CASE WHEN jsonb_typeof(o.offers) = 'array' THEN o.offers ELSE '[]'::jsonb END) e
      UNION ALL
      SELECT 'bids', e FROM jsonb_array_elements(
             CASE WHEN jsonb_typeof(o.bids) = 'array' THEN o.bids ELSE '[]'::jsonb END) e) x
    CROSS JOIN LATERAL (
      SELECT x.side,
             CASE WHEN coalesce(x.e->'px'->>'value', x.e->>'px', x.e->>'price') ~ '^[0-9]*\.?[0-9]+$'
                  THEN coalesce(x.e->'px'->>'value', x.e->>'px', x.e->>'price')::numeric END AS px,
             CASE WHEN coalesce(x.e->>'qty', x.e->>'size') ~ '^[0-9]*\.?[0-9]+$'
                  THEN coalesce(x.e->>'qty', x.e->>'size')::numeric END AS q) s),
agg AS (
  SELECT decision_id, strategy, refusal, verdict, holding_side, p, thr_pp, lv0, league,
         min(px) FILTER (WHERE side = 'offers' AND q > 0 AND px * 100 = round(px * 100)) AS ask_cent,
         min(px) FILTER (WHERE side = 'offers' AND q > 0) AS ask_any,
         max(px) FILTER (WHERE side = 'bids' AND q > 0 AND px * 100 = round(px * 100)) AS bid_cent,
         max(px) FILTER (WHERE side = 'bids' AND q > 0) AS bid_any,
         count(*) FILTER (WHERE px IS NOT NULL AND px * 100 <> round(px * 100)) AS subcent_levels
    FROM lv GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9),
r AS (
  SELECT *, CASE WHEN holding_side = 'SHORT' THEN 1 - bid_cent ELSE ask_cent END AS acq_cent,
            CASE WHEN holding_side = 'SHORT' THEN 1 - bid_any ELSE ask_any END AS acq_any
    FROM agg)
SELECT strategy, coalesce(refusal, 'ENTER') AS outcome, count(*) AS n,
       count(*) FILTER (WHERE acq_cent IS NULL) AS no_consumed_side,
       count(*) FILTER (WHERE abs(acq_cent - lv0::numeric) > 1e-9) AS level0_mismatch,
       round(max(abs(acq_cent - lv0::numeric)), 6) AS max_level0_diff,
       count(*) FILTER (WHERE subcent_levels > 0) AS rows_with_subcent_levels,
       count(*) FILTER (WHERE acq_any < acq_cent) AS subcent_better_than_cent,
       count(*) FILTER (WHERE acq_any < acq_cent AND (p::numeric - acq_cent) * 100 < thr_pp::numeric - 1e-9
                          AND (p::numeric - acq_any) * 100 >= thr_pp::numeric - 1e-9) AS subcent_would_clear,
       count(*) FILTER (WHERE ask_cent IS NOT NULL AND bid_cent IS NOT NULL AND bid_cent >= ask_cent) AS crossed_or_locked
  FROM r GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== E5 · THE OTHER SIDE OF THE SAME CONTRACT, on the same book (never evaluated as an entry) =='
-- LONG holding: the opposite is SHORT at 1 - best bid, probability 1 - p
-- (edge = bid - p). SHORT holding: the opposite is LONG at the best ask
-- (edge = 1 - p - ask). On a soccer per-side contract 1 - p(home) is p(draw) +
-- p(away): the complete-set de-vig, the contract's own NO payoff. The fee per
-- contract is 0.0695 x c x (1 - c) at that cost.
WITH d AS (
  SELECT d.decision_id, d.strategy, d.refusal, d.verdict, d.holding_side,
         d.p_pinnacle AS p, d.book_obs_id, d.us_market_slug,
         (d.policy_decision->>'threshold_edge_pp')::float8 AS thr_pp,
         (d.alternatives->'OPPOSITE_SIDE_SAME_MARKET'->>'gross_edge_pp')::float8 AS opp_pp_rec,
         split_part(d.us_market_slug, '-', 2) AS league, v.sport_family AS sport
    FROM paper_decisions d LEFT JOIN external_valuations v ON v.id = d.valuation_id
   WHERE d.decided_at > now() - interval '7 days' AND d.book_obs_id IS NOT NULL
     AND d.refusal = 'BELOW_MIN_GROSS_EDGE'),
b AS (
  SELECT d.*,
         (SELECT min(s.px) FROM (SELECT
              CASE WHEN coalesce(e->'px'->>'value', e->>'px', e->>'price') ~ '^[0-9]*\.?[0-9]+$'
                   THEN coalesce(e->'px'->>'value', e->>'px', e->>'price')::numeric END AS px,
              CASE WHEN coalesce(e->>'qty', e->>'size') ~ '^[0-9]*\.?[0-9]+$'
                   THEN coalesce(e->>'qty', e->>'size')::numeric END AS q
            FROM jsonb_array_elements(CASE WHEN jsonb_typeof(o.offers) = 'array' THEN o.offers
                                           ELSE '[]'::jsonb END) e) s
           WHERE s.q > 0 AND s.px * 100 = round(s.px * 100)) AS ask_cent,
         (SELECT max(s.px) FROM (SELECT
              CASE WHEN coalesce(e->'px'->>'value', e->>'px', e->>'price') ~ '^[0-9]*\.?[0-9]+$'
                   THEN coalesce(e->'px'->>'value', e->>'px', e->>'price')::numeric END AS px,
              CASE WHEN coalesce(e->>'qty', e->>'size') ~ '^[0-9]*\.?[0-9]+$'
                   THEN coalesce(e->>'qty', e->>'size')::numeric END AS q
            FROM jsonb_array_elements(CASE WHEN jsonb_typeof(o.bids) = 'array' THEN o.bids
                                           ELSE '[]'::jsonb END) e) s
           WHERE s.q > 0 AND s.px * 100 = round(s.px * 100)) AS bid_cent
    FROM d JOIN paper_book_observations o ON o.obs_id = d.book_obs_id),
r AS (
  SELECT *, CASE WHEN holding_side = 'SHORT' THEN ask_cent ELSE 1 - bid_cent END AS opp_cost
    FROM b),
e AS (
  SELECT *, ((1 - p::numeric) - opp_cost) * 100 AS opp_pp,
            ((1 - p::numeric) - opp_cost - 0.0695 * opp_cost * (1 - opp_cost)) * 100 AS opp_net_pp
    FROM r)
SELECT coalesce(sport, '?') AS sport, league, strategy, count(*) AS below_min,
       count(DISTINCT us_market_slug) AS markets,
       count(*) FILTER (WHERE opp_cost IS NULL) AS opp_side_empty,
       count(*) FILTER (WHERE opp_pp_rec IS NOT NULL AND abs(opp_pp_rec - opp_pp::float8) > 1e-6) AS opp_recorded_mismatch,
       count(*) FILTER (WHERE opp_pp >= thr_pp::numeric - 1e-9) AS opp_clears_threshold,
       count(DISTINCT us_market_slug) FILTER (WHERE opp_pp >= thr_pp::numeric - 1e-9) AS opp_clears_markets,
       count(*) FILTER (WHERE opp_pp >= thr_pp::numeric - 1e-9 AND opp_net_pp > 0) AS opp_clears_net_of_fee,
       count(DISTINCT us_market_slug) FILTER (WHERE opp_pp >= thr_pp::numeric - 1e-9 AND opp_net_pp > 0) AS opp_net_markets,
       round(max(opp_pp), 4) AS opp_pp_max,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY opp_pp)::numeric, 4) AS opp_pp_p50
  FROM e GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== E6 · the probability inputs behind those decisions (outcome set, side, method) =='
WITH d AS (
  SELECT DISTINCT d.valuation_id, d.holding_side
    FROM paper_decisions d
   WHERE d.decided_at > now() - interval '7 days'
     AND (d.verdict = 'ENTER' OR d.refusal IN ('BELOW_MIN_GROSS_EDGE',
          'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT',
          'NET_EV_NOT_POSITIVE_AFTER_FEES', 'NO_SIZED_QUANTITY'))),
v AS (
  SELECT v.*, d.holding_side,
         (SELECT count(*) FROM jsonb_object_keys(CASE WHEN jsonb_typeof(v.raw_odds) = 'object'
                                                      THEN v.raw_odds ELSE '{}'::jsonb END)) AS n_odds,
         (SELECT sum(1 / (x.value)::text::numeric) FROM jsonb_each(CASE WHEN jsonb_typeof(v.raw_odds) = 'object'
                                                                       THEN v.raw_odds ELSE '{}'::jsonb END) x
           WHERE jsonb_typeof(x.value) = 'number' AND (x.value)::text::numeric > 1) AS sum_q,
         CASE WHEN jsonb_typeof(v.raw_odds -> v.contract_selection) = 'number'
              THEN 1 / (v.raw_odds ->> v.contract_selection)::numeric END AS q_sel
    FROM d JOIN external_valuations v ON v.id = d.valuation_id)
SELECT sport_family, provider, devig_method, outcomes_priced, expected_outcomes, n_odds,
       count(*) AS valuations,
       count(*) FILTER (WHERE q_sel IS NULL) AS selection_not_in_odds,
       count(*) FILTER (WHERE payout_is_complement) AS complement_rows,
       count(*) FILTER (WHERE payout_event IS DISTINCT FROM contract_selection) AS payout_ne_selection,
       count(*) FILTER (WHERE (holding_side = 'LONG') <> (buy_intent = 'ORDER_INTENT_BUY_LONG')) AS side_intent_disagree,
       count(*) FILTER (WHERE buy_intent = 'ORDER_INTENT_BUY_SHORT') AS short_intent_rows,
       round(min(sum_q - 1), 5) AS overround_min, round(max(sum_q - 1), 5) AS overround_max,
       round(max(abs((sum_q - 1) - overround::numeric)), 9) AS overround_vs_recorded_max_diff,
       round(avg(probability::numeric - q_sel / sum_q), 6) AS mean_stored_minus_multiplicative,
       round(max(abs(probability::numeric - q_sel / sum_q)), 6) AS max_abs_stored_minus_multiplicative,
       round(min(probability)::numeric, 4) AS p_min, round(max(probability)::numeric, 4) AS p_max
  FROM v GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 2, 3, 4, 5, 6;

\echo '== E6b · soccer: which venue contract carries the HOME selection (per-side LONG expected) =='
WITH d AS (
  SELECT DISTINCT d.valuation_id FROM paper_decisions d
   WHERE d.decided_at > now() - interval '7 days'
     AND (d.verdict = 'ENTER' OR d.refusal IN ('BELOW_MIN_GROSS_EDGE',
          'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT',
          'NET_EV_NOT_POSITIVE_AFTER_FEES', 'NO_SIZED_QUANTITY')))
SELECT v.sport_family, split_part(v.us_market_slug, '-', 1) AS slug_family, v.buy_intent, v.ladder_side,
       v.contract_identity_basis, count(*) AS n,
       count(*) FILTER (WHERE lower(v.us_market_slug) LIKE '%-draw') AS draw_contract_rows,
       min(v.us_market_slug) AS example_slug, min(v.contract_selection) AS example_selection,
       min(v.matched_side_norm) AS example_matched_side
  FROM d JOIN external_valuations v ON v.id = d.valuation_id
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3, 4, 5;

\echo '== E6c · was the selection ever anything but the provider HOME team? (the only priced outcome) =='
SELECT v.sport_family, count(*) AS valuations_7d,
       count(*) FILTER (WHERE v.probability IS NOT NULL) AS priced,
       count(DISTINCT v.us_market_slug) AS contracts,
       count(DISTINCT coalesce(v.settlement_comparison->'reference_input'->>'discovery_event_id', v.event_key)) AS provider_events,
       count(*) FILTER (WHERE v.settlement_comparison->'reference_input' ? 'discovery_home'
                          AND v.contract_selection = v.settlement_comparison->'reference_input'->>'discovery_home') AS selection_is_home,
       count(*) FILTER (WHERE v.settlement_comparison->'reference_input' ? 'discovery_away'
                          AND v.contract_selection = v.settlement_comparison->'reference_input'->>'discovery_away') AS selection_is_away
  FROM external_valuations v
 WHERE v.decided_at > now() - interval '7 days'
   AND v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
 GROUP BY 1 ORDER BY 1;

\echo '== E7 · raw odds of every valuation behind ENTER / BELOW_MIN (offline power de-vig recompute) =='
SELECT 'E7|' || json_build_object(
         'id', v.id, 's', v.sport_family, 'sel', v.contract_selection, 'odds', v.raw_odds,
         'p', v.probability, 'm', v.devig_method, 'c', v.payout_is_complement,
         'prov', v.provider, 'ov', v.overround, 'n', v.outcomes_priced, 'x', v.expected_outcomes,
         'am', v.settlement_comparison->'reference_input'->'raw_odds',
         'lab', v.settlement_comparison->'reference_input'->'outcome_labels')::text AS row
  FROM external_valuations v
 WHERE v.id IN (SELECT d.valuation_id FROM paper_decisions d
                 WHERE d.decided_at > now() - interval '7 days'
                   AND (d.verdict = 'ENTER' OR d.refusal = 'BELOW_MIN_GROSS_EDGE'))
 ORDER BY v.id DESC LIMIT 6000;

\echo '== E8 · stratified decision sample with every recorded input (all ENTER; <= 6 per strategy x league x refusal) =='
WITH d AS (
  SELECT d.*, row_number() OVER (PARTITION BY d.strategy, split_part(d.us_market_slug, '-', 2),
                                              coalesce(d.refusal, 'ENTER')
                                 ORDER BY d.decided_at DESC) AS rk
    FROM paper_decisions d
   WHERE d.decided_at > now() - interval '7 days'
     AND (d.verdict = 'ENTER' OR d.refusal IN ('BELOW_MIN_GROSS_EDGE',
          'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT',
          'NET_EV_NOT_POSITIVE_AFTER_FEES', 'NO_SIZED_QUANTITY')))
SELECT 'E8|' || json_build_object(
         'did', d.decision_id, 'st', d.strategy, 'pv', d.policy_version, 'at', extract(epoch FROM d.decided_at),
         'verdict', d.verdict, 'refusal', d.refusal, 'hs', d.holding_side, 'intent', d.intent,
         'slug', d.us_market_slug, 'p', d.p_pinnacle, 'vid', d.valuation_id,
         'thr', d.policy_decision->'threshold_edge_pp', 'ge', d.policy_decision->'gross_edge_pp',
         'net', d.policy_decision->'net_expected_profit_usd', 'fees', d.policy_decision->'fees_usd',
         'levels', d.economics->'levels', 'walk', d.economics->'acquisition'->'walk',
         'acq_qty', d.economics->'acquisition'->'qty', 'fee_basis', d.economics->'acquisition'->'fee_basis',
         'fee_stop', d.economics->'fee_stop', 'book_age', d.economics->'book_age_s',
         'opp', d.alternatives->'OPPOSITE_SIDE_SAME_MARKET', 'qty', d.proposed_qty, 'limit', d.limit_price,
         'pin_age', d.pinnacle->'age_s', 'pin_method', d.pinnacle->'method',
         'obs', d.book_obs_id, 'obs_at', extract(epoch FROM o.observed_at), 'obs_src', o.source,
         'offers', (SELECT jsonb_agg(e ORDER BY i) FROM jsonb_array_elements(
                       CASE WHEN jsonb_typeof(o.offers) = 'array' THEN o.offers ELSE '[]'::jsonb END)
                       WITH ORDINALITY t(e, i) WHERE i <= 8),
         'bids', (SELECT jsonb_agg(e ORDER BY i) FROM jsonb_array_elements(
                     CASE WHEN jsonb_typeof(o.bids) = 'array' THEN o.bids ELSE '[]'::jsonb END)
                     WITH ORDINALITY t(e, i) WHERE i <= 8),
         'v', json_build_object('sport', v.sport_family, 'sel', v.contract_selection, 'odds', v.raw_odds,
                                'p', v.probability, 'm', v.devig_method, 'c', v.payout_is_complement,
                                'pay', v.payout_event, 'bi', v.buy_intent, 'ladder', v.ladder_side,
                                'prov', v.provider, 'n', v.outcomes_priced, 'x', v.expected_outcomes,
                                'ov', v.overround, 'obs_at', extract(epoch FROM v.observed_at),
                                'dec_at', extract(epoch FROM v.decided_at), 'side_norm', v.matched_side_norm,
                                'am', v.settlement_comparison->'reference_input'->'raw_odds',
                                'lab', v.settlement_comparison->'reference_input'->'outcome_labels',
                                'home', v.settlement_comparison->'reference_input'->'discovery_home',
                                'away', v.settlement_comparison->'reference_input'->'discovery_away'))::text AS row
  FROM d
  LEFT JOIN paper_book_observations o ON o.obs_id = d.book_obs_id
  LEFT JOIN external_valuations v ON v.id = d.valuation_id
 WHERE (d.verdict = 'ENTER' AND d.rk <= 400) OR d.rk <= 6
 ORDER BY d.strategy, d.decided_at DESC
 LIMIT 1500;
