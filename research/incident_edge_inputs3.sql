-- P0 INCIDENT (inc-edge), READ-ONLY, THIRD READ: what each candidate repair
-- would change, counted on the recorded production rows (7 d), BEFORE any
-- code changes. The completed-game rule is applied exactly as it runs:
--   gross   p - c >= threshold_edge_pp / 100 at the level used
--   net     p - c - 0.0695 x c x (1 - c) > 0 at the level used (V2/V3)
-- R1  the OTHER SIDE of the same venue contract on the same book (never
--     evaluated): probability 1 - p (complete-set complement), cost 1 - best
--     bid for a LONG holding / best ask for a SHORT holding.
-- R2  HALF-CENT LEVELS: a level off the cent grid is reachable only through a
--     whole-cent limit (the adapter formats %.2f) at the next cent in the
--     paying direction; it may be used only when THAT limit clears.
-- R3  the de-vig method's effect on classification (information only).
\echo '== G0 · read instant; record purpose of the valuations behind the decisions; table size =='
SELECT now() AS read_at, (SELECT count(*) FROM external_valuations) AS valuation_rows,
       pg_size_pretty(pg_total_relation_size('external_valuations')) AS valuation_table_size;
SELECT v.record_purpose, v.sport_family, count(*) AS valuations,
       count(*) FILTER (WHERE v.admissible) AS admissible
  FROM external_valuations v
 WHERE v.id IN (SELECT d.valuation_id FROM paper_decisions d
                 WHERE d.decided_at > now() - interval '7 days'
                   AND d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER')
 GROUP BY 1, 2 ORDER BY 1, 2;
SELECT indexname, indexdef FROM pg_indexes
 WHERE tablename = 'external_valuations' AND indexdef ILIKE '%unique%';

\echo '== G1 · R1 + R2 per completed-game decision (7 d), by sport / league / policy version =='
WITH d AS (
  SELECT d.decision_id, d.policy_version, d.refusal, d.verdict, d.holding_side,
         d.p_pinnacle::numeric AS p, d.book_obs_id, d.us_market_slug,
         ((d.policy_decision->>'threshold_edge_pp')::numeric) / 100 AS thr,
         split_part(d.us_market_slug, '-', 2) AS league, v.sport_family AS sport
    FROM paper_decisions d LEFT JOIN external_valuations v ON v.id = d.valuation_id
   WHERE d.decided_at > now() - interval '7 days' AND d.book_obs_id IS NOT NULL
     AND d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
     AND (d.verdict = 'ENTER' OR d.refusal IN ('BELOW_MIN_GROSS_EDGE',
          'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT'))),
lv AS (
  SELECT d.decision_id, x.side,
         CASE WHEN coalesce(x.e->'px'->>'value', x.e->>'px', x.e->>'price') ~ '^[0-9]*\.?[0-9]+$'
              THEN coalesce(x.e->'px'->>'value', x.e->>'px', x.e->>'price')::numeric END AS px,
         CASE WHEN coalesce(x.e->>'qty', x.e->>'size') ~ '^[0-9]*\.?[0-9]+$'
              THEN coalesce(x.e->>'qty', x.e->>'size')::numeric END AS q
    FROM d JOIN paper_book_observations o ON o.obs_id = d.book_obs_id
    CROSS JOIN LATERAL (
      SELECT 'offers' AS side, e FROM jsonb_array_elements(
             CASE WHEN jsonb_typeof(o.offers) = 'array' THEN o.offers ELSE '[]'::jsonb END) e
      UNION ALL
      SELECT 'bids', e FROM jsonb_array_elements(
             CASE WHEN jsonb_typeof(o.bids) = 'array' THEN o.bids ELSE '[]'::jsonb END) e) x),
a AS (
  SELECT decision_id,
         min(px) FILTER (WHERE side = 'offers' AND q > 0 AND px * 100 = round(px * 100)) AS ask_c,
         min(px) FILTER (WHERE side = 'offers' AND q > 0) AS ask_a,
         max(px) FILTER (WHERE side = 'bids' AND q > 0 AND px * 100 = round(px * 100)) AS bid_c,
         max(px) FILTER (WHERE side = 'bids' AND q > 0) AS bid_a
    FROM lv GROUP BY 1),
c AS (
  -- costs in cost space for OUR side (s_*) and the OTHER side (o_*); "c" =
  -- best cent-grid level (what the code uses), "a" = best level of any tick,
  -- "r" = the whole-cent limit that reaches the best level of any tick
  SELECT d.*,
         CASE WHEN d.holding_side = 'SHORT' THEN 1 - a.bid_c ELSE a.ask_c END AS s_c,
         CASE WHEN d.holding_side = 'SHORT' THEN 1 - a.bid_a ELSE a.ask_a END AS s_a,
         CASE WHEN d.holding_side = 'SHORT' THEN 1 - floor(a.bid_a * 100) / 100
              ELSE ceil(a.ask_a * 100) / 100 END AS s_r,
         CASE WHEN d.holding_side = 'SHORT' THEN a.ask_c ELSE 1 - a.bid_c END AS o_c,
         CASE WHEN d.holding_side = 'SHORT' THEN a.ask_a ELSE 1 - a.bid_a END AS o_a,
         CASE WHEN d.holding_side = 'SHORT' THEN ceil(a.ask_a * 100) / 100
              ELSE 1 - floor(a.bid_a * 100) / 100 END AS o_r
    FROM d JOIN a USING (decision_id)),
q AS (
  SELECT *,
         -- the rule at a cost x: gross AND net-of-fee
         (p - s_c >= thr - 1e-9 AND p - s_c - 0.0695 * s_c * (1 - s_c) > 0) AS own_cent_ok,
         (s_a < s_c AND p - s_r >= thr - 1e-9 AND p - s_r - 0.0695 * s_r * (1 - s_r) > 0) AS own_half_reach_ok,
         (s_a < s_c AND p - s_a >= thr - 1e-9) AS own_half_gross_ok,
         ((1 - p) - o_c >= thr - 1e-9 AND (1 - p) - o_c - 0.0695 * o_c * (1 - o_c) > 0) AS opp_cent_ok,
         ((1 - p) - o_c >= thr - 1e-9) AS opp_cent_gross_ok,
         (o_a < o_c AND (1 - p) - o_r >= thr - 1e-9
                    AND (1 - p) - o_r - 0.0695 * o_r * (1 - o_r) > 0) AS opp_half_reach_ok
    FROM c)
SELECT coalesce(sport, '?') AS sport, league, policy_version, coalesce(refusal, 'ENTER') AS outcome,
       count(*) AS n, count(DISTINCT us_market_slug) AS markets,
       count(*) FILTER (WHERE own_cent_ok) AS own_cent_rule_ok,
       count(*) FILTER (WHERE s_a < s_c) AS own_best_is_half_cent,
       count(*) FILTER (WHERE own_half_gross_ok) AS own_half_gross_only,
       count(*) FILTER (WHERE NOT own_cent_ok AND own_half_reach_ok) AS r2_flips_to_qualified,
       count(DISTINCT us_market_slug) FILTER (WHERE NOT own_cent_ok AND own_half_reach_ok) AS r2_flip_markets,
       count(*) FILTER (WHERE opp_cent_gross_ok) AS r1_opp_gross_ok,
       count(*) FILTER (WHERE opp_cent_ok) AS r1_opp_qualifies,
       count(DISTINCT us_market_slug) FILTER (WHERE opp_cent_ok) AS r1_opp_markets,
       count(*) FILTER (WHERE opp_cent_ok OR opp_half_reach_ok) AS r1_r2_opp_qualifies,
       count(*) FILTER (WHERE own_cent_ok AND opp_cent_ok) AS both_sides_qualify
  FROM q GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

\echo '== G2 · R1 distinct opportunities: (market, hour) buckets where the OTHER side qualified =='
WITH d AS (
  SELECT d.decision_id, d.decided_at, d.holding_side, d.p_pinnacle::numeric AS p, d.book_obs_id,
         d.us_market_slug, ((d.policy_decision->>'threshold_edge_pp')::numeric) / 100 AS thr,
         d.refusal, v.sport_family AS sport
    FROM paper_decisions d LEFT JOIN external_valuations v ON v.id = d.valuation_id
   WHERE d.decided_at > now() - interval '7 days' AND d.book_obs_id IS NOT NULL
     AND d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
     AND d.policy_version = 'PINNACLE_COMPLETED_GAME_PAPER_V3'
     AND d.refusal IN ('BELOW_MIN_GROSS_EDGE', 'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT')),
b AS (
  SELECT d.*,
         (SELECT min(s.px) FROM (SELECT
              CASE WHEN coalesce(e->'px'->>'value', e->>'px') ~ '^[0-9]*\.?[0-9]+$'
                   THEN coalesce(e->'px'->>'value', e->>'px')::numeric END AS px,
              CASE WHEN e->>'qty' ~ '^[0-9]*\.?[0-9]+$' THEN (e->>'qty')::numeric END AS q
            FROM jsonb_array_elements(CASE WHEN jsonb_typeof(o.offers) = 'array' THEN o.offers
                                           ELSE '[]'::jsonb END) e) s
           WHERE s.q > 0 AND s.px * 100 = round(s.px * 100)) AS ask_c,
         (SELECT max(s.px) FROM (SELECT
              CASE WHEN coalesce(e->'px'->>'value', e->>'px') ~ '^[0-9]*\.?[0-9]+$'
                   THEN coalesce(e->'px'->>'value', e->>'px')::numeric END AS px,
              CASE WHEN e->>'qty' ~ '^[0-9]*\.?[0-9]+$' THEN (e->>'qty')::numeric END AS q
            FROM jsonb_array_elements(CASE WHEN jsonb_typeof(o.bids) = 'array' THEN o.bids
                                           ELSE '[]'::jsonb END) e) s
           WHERE s.q > 0 AND s.px * 100 = round(s.px * 100)) AS bid_c
    FROM d JOIN paper_book_observations o ON o.obs_id = d.book_obs_id),
e AS (
  SELECT *, CASE WHEN holding_side = 'SHORT' THEN ask_c ELSE 1 - bid_c END AS oc FROM b),
k AS (
  SELECT * FROM e WHERE (1 - p) - oc >= thr - 1e-9 AND (1 - p) - oc - 0.0695 * oc * (1 - oc) > 0)
SELECT sport, us_market_slug, date_trunc('hour', decided_at) AS hour, count(*) AS rows,
       round(max(((1 - p) - oc) * 100), 3) AS best_opp_gross_pp,
       round(max(((1 - p) - oc - 0.0695 * oc * (1 - oc)) * 100), 3) AS best_opp_net_pp,
       min(round(oc, 4)) AS opp_cost, round(min(1 - p), 4) AS opp_p_min, round(max(1 - p), 4) AS opp_p_max
  FROM k GROUP BY 1, 2, 3 ORDER BY 3 DESC LIMIT 80;

\echo '== G3 · R3 the de-vig method: classification under multiplicative / additive vs power (gross + net, cent best) =='
WITH d AS (
  SELECT d.decision_id, d.refusal, d.verdict, d.holding_side, d.p_pinnacle::float8 AS p,
         ((d.policy_decision->>'threshold_edge_pp')::float8) / 100 AS thr,
         (d.economics->'levels'->0->>'price')::float8 AS c, v.sport_family AS sport,
         v.raw_odds, v.contract_selection
    FROM paper_decisions d JOIN external_valuations v ON v.id = d.valuation_id
   WHERE d.decided_at > now() - interval '7 days'
     AND d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
     AND d.policy_version = 'PINNACLE_COMPLETED_GAME_PAPER_V3'
     AND (d.verdict = 'ENTER' OR d.refusal IN ('BELOW_MIN_GROSS_EDGE',
          'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT'))),
m AS (
  SELECT d.*,
         (SELECT sum(1 / (x.value)::text::float8) FROM jsonb_each(d.raw_odds) x) AS sq,
         (SELECT count(*) FROM jsonb_each(d.raw_odds) x) AS n,
         1 / (d.raw_odds ->> d.contract_selection)::float8 AS qs
    FROM d WHERE jsonb_typeof(d.raw_odds) = 'object' AND d.c IS NOT NULL),
r AS (
  SELECT *, qs / sq AS p_mult, qs - (sq - 1) / n AS p_add FROM m),
f AS (
  SELECT *, (p - c >= thr - 1e-9 AND p - c - 0.0695 * c * (1 - c) > 0) AS ok_power,
            (p_mult - c >= thr - 1e-9 AND p_mult - c - 0.0695 * c * (1 - c) > 0) AS ok_mult,
            (p_add - c >= thr - 1e-9 AND p_add - c - 0.0695 * c * (1 - c) > 0) AS ok_add
    FROM r)
SELECT sport, coalesce(refusal, 'ENTER') AS outcome, count(*) AS n,
       count(*) FILTER (WHERE ok_power) AS ok_power_recomputed,
       count(*) FILTER (WHERE ok_mult) AS ok_multiplicative,
       count(*) FILTER (WHERE ok_power AND NOT ok_mult) AS power_only,
       count(*) FILTER (WHERE ok_mult AND NOT ok_power) AS multiplicative_only,
       count(*) FILTER (WHERE ok_add) AS ok_additive,
       count(*) FILTER (WHERE ok_add AND NOT ok_power) AS additive_only,
       round(avg(p_mult - p)::numeric, 5) AS mean_mult_minus_power
  FROM f GROUP BY 1, 2 ORDER BY 1, 2;
