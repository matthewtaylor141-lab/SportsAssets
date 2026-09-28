-- READ-ONLY. WHERE THE FAIR-VALUE CHAIN ACTUALLY BREAKS.
--
-- Priority 2 of the completion brief: "Identify where provider evidence,
-- mapping, devig, calibration, persistence or consumer lookup fails.
-- Separate missing engineering from unavailable evidence using current
-- candidate records."
--
-- THE CHAIN, read off the code before writing this query:
--
--   1 provider   workers/ext_pinnacle_loop.fetch_odds() -> the-odds-api,
--                LIVE each cycle. `observed_at` is the PROVIDER's own
--                `last_update`, never our read time (deliberately: our
--                read time would make every quote fresh by construction).
--   2 mapping    bettor_pinnacle_devig.map_selection() + vmap.map_event()
--   3 devig      bettor_pinnacle_devig.valuation() -> probability
--   4 complement _p_pay = 1 - p(selection) for a BUY_SHORT leg
--   5 persist    external_valuations
--   6 consume    bettor_entry_gate.admit(fair_value=...) and
--                bettor_entry_execution.estimate(fair_value=...)
--
-- WHAT THE CODE ALREADY TELLS US, so this query does not re-ask it:
-- `valuation()` RETURNS EARLY on QUOTE_STALE, before `map_selection`. So
-- on a stale quote there is no probability, therefore no fair value,
-- therefore no break-even limit and no execution estimate. My earlier
-- census listed "no independent fair value" (399 rows) and QUOTE_STALE
-- (399 rows) as TWO blockers with the same row count and the same owner.
-- They are one cause and its consequence. This query is written to
-- confirm or refute that, not to assume it.
--
-- AND THE ONE THING ONLY THE ROWS CAN SETTLE: whether the staleness is
-- OURS or the PROVIDER'S. The row carries both timestamps, so the age
-- decomposes exactly:
--
--     provider_lag = received_at - observed_at   (already old on arrival)
--     our_lag      = age_s - provider_lag        (time we then spent)
--
-- If provider_lag alone exceeds 30 s, the evidence is not available at
-- the freshness this engine requires and no amount of engineering here
-- changes it. If our_lag dominates, it is our latency and it is fixable.
-- That distinction is the whole point of this file.


-- ─────────────────────────────────────────────────────────────────────
-- 0 · The population, so every later percentage has a denominator.
-- ─────────────────────────────────────────────────────────────────────
SELECT count(*)                                    AS rows_total,
       count(*) FILTER (WHERE probability IS NOT NULL) AS with_probability,
       count(*) FILTER (WHERE probability IS NULL)     AS without_probability,
       count(DISTINCT us_market_slug)               AS distinct_markets,
       min(decided_at)                              AS earliest,
       max(decided_at)                              AS latest
  FROM external_valuations;

-- ─────────────────────────────────────────────────────────────────────
-- 1 · Is the absent fair value the SAME rows as QUOTE_STALE, or not?
--     A cross-tab, because equal counts do not establish equal sets.
-- ─────────────────────────────────────────────────────────────────────
SELECT (probability IS NULL)                              AS no_fair_value,
       ('QUOTE_STALE' = ANY(refusals))                    AS quote_stale,
       ('QUOTE_HAS_NO_TIMESTAMP' = ANY(refusals))         AS no_timestamp,
       count(*)                                           AS rows_
  FROM external_valuations
 GROUP BY 1, 2, 3
 ORDER BY rows_ DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 2 · THE DECISIVE ONE. Among rows with no probability, WHICH link in
--     the chain refused first. `valuation()` returns at the first
--     failure, so the earliest-stage code present IS the first break.
-- ─────────────────────────────────────────────────────────────────────
SELECT CASE
         WHEN 'MARKET_NOT_IN_SUPPORTED_SET'   = ANY(refusals)
              THEN '1_provider_scope'
         WHEN 'PINNACLE_NOT_IN_THIS_PAYLOAD'  = ANY(refusals)
              THEN '1_provider_book_absent'
         WHEN 'OUTCOME_SET_INCOMPLETE'        = ANY(refusals)
              THEN '1_provider_partial_outcomes'
         WHEN 'ODDS_NOT_A_PRICE'              = ANY(refusals)
              THEN '1_provider_bad_odds'
         WHEN 'QUOTE_HAS_NO_TIMESTAMP'        = ANY(refusals)
              THEN '2_quote_unageable'
         WHEN 'QUOTE_STALE'                   = ANY(refusals)
              THEN '2_quote_stale'
         WHEN 'MAPPING_NOT_ESTABLISHED'       = ANY(refusals)
              THEN '3_mapping_absent'
         WHEN 'MAPPING_AMBIGUOUS'             = ANY(refusals)
              THEN '3_mapping_ambiguous'
         WHEN 'SELECTION_NOT_IN_OUTCOME_SET'  = ANY(refusals)
              THEN '3_selection_unmatched'
         WHEN 'PERIOD_DOES_NOT_MATCH'         = ANY(refusals)
              THEN '3_period_mismatch'
         WHEN 'LINE_DOES_NOT_MATCH'           = ANY(refusals)
              THEN '3_line_mismatch'
         WHEN 'SETTLEMENT_RULE_DOES_NOT_MATCH' = ANY(refusals)
              THEN '3_settlement_mismatch'
         WHEN 'DEVIG_METHOD_NOT_DECLARED'     = ANY(refusals)
              THEN '4_devig_method'
         ELSE '9_NO_NAMED_CAUSE__THIS_WOULD_BE_A_GAP_IN_THE_CODES'
       END                       AS first_failing_link,
       count(*)                  AS rows_,
       count(DISTINCT us_market_slug) AS markets,
       round(min(age_s)::numeric, 1) AS min_age_s,
       round(avg(age_s)::numeric, 1) AS avg_age_s,
       round(max(age_s)::numeric, 1) AS max_age_s
  FROM external_valuations
 WHERE probability IS NULL
 GROUP BY 1
 ORDER BY rows_ DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 3 · OURS OR THE PROVIDER'S? The age decomposed on the stale rows.
--     `provider_lag` is how old the quote already was when it reached
--     us; `our_lag` is what we added. Reported as a distribution, not a
--     mean, because a mean cannot distinguish "always 45 s" from "mostly
--     5 s with a long tail" and those have different fixes.
-- ─────────────────────────────────────────────────────────────────────
WITH d AS (
    SELECT id, age_s,
           extract(epoch FROM (received_at - observed_at)) AS provider_lag,
           age_s - extract(epoch FROM (received_at - observed_at)) AS our_lag
      FROM external_valuations
     WHERE 'QUOTE_STALE' = ANY(refusals)
       AND observed_at IS NOT NULL AND received_at IS NOT NULL
)
SELECT count(*)                                        AS stale_rows_measurable,
       round(min(provider_lag)::numeric, 1)            AS provider_lag_min,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_lag)::numeric, 1)
                                                       AS provider_lag_median,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY provider_lag)::numeric, 1)
                                                       AS provider_lag_p95,
       round(max(provider_lag)::numeric, 1)            AS provider_lag_max,
       round(min(our_lag)::numeric, 1)                 AS our_lag_min,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY our_lag)::numeric, 1)
                                                       AS our_lag_median,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY our_lag)::numeric, 1)
                                                       AS our_lag_p95,
       round(max(our_lag)::numeric, 1)                 AS our_lag_max,
       -- THE VERDICT, counted rather than argued.
       count(*) FILTER (WHERE provider_lag > 30.0)     AS already_stale_on_arrival,
       count(*) FILTER (WHERE provider_lag <= 30.0
                          AND age_s > 30.0)            AS we_made_it_stale
  FROM d;

-- ─────────────────────────────────────────────────────────────────────
-- 4 · And the same split per day, so a fix is aimed at a cause rather
--     than an average, and an intermittent problem is not read as a
--     structural one.
-- ─────────────────────────────────────────────────────────────────────
WITH d AS (
    SELECT decided_at::date AS day, age_s,
           extract(epoch FROM (received_at - observed_at)) AS provider_lag
      FROM external_valuations
     WHERE observed_at IS NOT NULL AND received_at IS NOT NULL
)
SELECT day, count(*) AS rows_,
       count(*) FILTER (WHERE provider_lag > 30.0)  AS already_stale_on_arrival,
       count(*) FILTER (WHERE provider_lag <= 30.0
                          AND age_s > 30.0)         AS we_made_it_stale,
       count(*) FILTER (WHERE age_s <= 30.0)        AS fresh_enough,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_lag)::numeric, 1)
                                                    AS provider_lag_median
  FROM d
 GROUP BY day
 ORDER BY day;

-- ─────────────────────────────────────────────────────────────────────
-- 5 · THE ROWS THAT DID GET A FAIR VALUE. What distinguishes them is
--     the evidence that the chain CAN complete -- and their ages bound
--     how fresh this provider's quotes ever are.
-- ─────────────────────────────────────────────────────────────────────
SELECT count(*)                                     AS rows_with_probability,
       count(DISTINCT us_market_slug)               AS markets,
       count(DISTINCT sport_family)                 AS sports,
       round(min(age_s)::numeric, 1)                AS age_min,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY age_s)::numeric, 1)
                                                    AS age_median,
       round(max(age_s)::numeric, 1)                AS age_max,
       count(*) FILTER (WHERE execution_estimate IS NOT NULL) AS with_exec_estimate,
       count(*) FILTER (WHERE estimated_edge_per_contract IS NOT NULL)
                                                    AS with_an_edge,
       count(*) FILTER (WHERE estimated_edge_per_contract > 0)
                                                    AS with_a_positive_edge,
       count(*) FILTER (WHERE admissible)           AS admissible
  FROM external_valuations
 WHERE probability IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────
-- 6 · CONSUMER LOOKUP, checked separately from production. A fair value
--     that is persisted but never read is a different failure from one
--     that is never produced. Does any row that HAS a probability also
--     carry the gate's own "no independent fair value" refusals? If so,
--     the break is at the consumer, not upstream.
-- ─────────────────────────────────────────────────────────────────────
SELECT count(*) AS rows_with_probability_but_gate_says_no_fv,
       array_agg(DISTINCT u) AS which_codes
  FROM external_valuations, unnest(refusals) AS u
 WHERE probability IS NOT NULL
   AND u IN ('INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED',
             'FAIR_VALUE_IS_THE_VENUE_BENCHMARK',
             'NO_QUALIFIED_MODEL',
             'BREAK_EVEN_LIMIT_NOT_COMPUTABLE_WITHOUT_A_VALUATION');

-- ─────────────────────────────────────────────────────────────────────
-- 7 · The full refusal census on rows that DID get a fair value, so the
--     next blocker after the fair value is named rather than guessed.
-- ─────────────────────────────────────────────────────────────────────
SELECT u AS code, count(*) AS rows_
  FROM external_valuations, unnest(refusals) AS u
 WHERE probability IS NOT NULL
 GROUP BY u
 ORDER BY rows_ DESC
 LIMIT 30;
