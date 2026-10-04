-- P0 INCIDENT, segment MARKET-FAMILY / SETTLEMENT / PROBABILITY / FRESHNESS,
-- third read. READ-ONLY.
--
-- H1  valuation rows with NO provider observation time, 7 days, by family x
--     league x the de-vig refusal on the row. The uniqueness index
--     external_valuations_one_per_observation (migration 105) coalesces a NULL
--     observed_at to -infinity, so every later observation of such a contract
--     collides with the first row and is skipped.
-- H2  collector ledger rows whose codes record a skipped duplicate observation,
--     24 h, by provider sport x first refusal.
-- H3  QUOTE_STALE valuation rows, 24 h, by provider x the PinnAPI fallback reason
--     that sent the row to the slower provider.
-- H4  collector ledger rows carrying QUOTE_STALE_ON_ARRIVAL, 24 h, by sport: the
--     provider-minus-receipt clock recorded on the row's codes is not stored, so
--     only counts are given.
\echo '== H0 · read instant =='
SELECT now() AS read_at;

\echo '== H1 · valuations with observed_at NULL, 7 days: family x league x de-vig refusal =='
SELECT v.sport_family, lower(split_part(coalesce(v.us_market_slug, ''), '-', 2)) AS league,
       coalesce((SELECT r FROM unnest(v.refusals) r
                  WHERE r IN ('MARKET_NOT_IN_SUPPORTED_SET', 'PINNACLE_NOT_IN_THIS_PAYLOAD',
                              'OUTCOME_SET_INCOMPLETE', 'ODDS_NOT_A_PRICE', 'MAPPING_NOT_ESTABLISHED',
                              'MAPPING_AMBIGUOUS', 'SELECTION_NOT_IN_OUTCOME_SET', 'LINE_DOES_NOT_MATCH',
                              'PERIOD_DOES_NOT_MATCH', 'SETTLEMENT_RULE_DOES_NOT_MATCH',
                              'QUOTE_HAS_NO_TIMESTAMP') LIMIT 1), '<other>') AS devig_refusal,
       count(*) AS rows, count(DISTINCT v.us_market_slug) AS contracts,
       min(v.decided_at) AS first_at, max(v.decided_at) AS last_at
  FROM external_valuations v
 WHERE v.decided_at > now() - interval '7 days' AND v.observed_at IS NULL
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;

\echo '== H1b · same 7 days: rows per contract where observed_at IS NULL vs NOT NULL =='
SELECT v.sport_family, (v.observed_at IS NULL) AS no_clock,
       count(*) AS rows, count(DISTINCT v.us_market_slug) AS contracts,
       round(count(*)::numeric / greatest(count(DISTINCT v.us_market_slug), 1), 2) AS rows_per_contract
  FROM external_valuations v
 WHERE v.decided_at > now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== H2 · ledger rows recording a skipped duplicate observation, 24 h =='
SELECT sport_key, coalesce(first_refusal, '-') AS first_refusal, outcome,
       count(*) AS rows, count(DISTINCT provider_event_id) AS events
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
   AND codes ? 'DUPLICATE_OBSERVATION_SKIPPED'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;

\echo '== H3 · QUOTE_STALE valuation rows, 24 h, by provider x PinnAPI fallback reason =='
SELECT v.sport_family, v.provider,
       coalesce(v.settlement_comparison->'reference_input'->>'fallback_reason', '-') AS pinnapi_fallback,
       count(*) AS rows, count(DISTINCT v.us_market_slug) AS contracts
  FROM external_valuations v
 WHERE v.decided_at > now() - interval '24 hours' AND 'QUOTE_STALE' = ANY(v.refusals)
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;

\echo '== H4 · stale-on-arrival ledger rows and events, 24 h, by sport =='
SELECT sport_key, count(*) AS rows, count(DISTINCT provider_event_id) AS events,
       count(*) FILTER (WHERE codes ? 'QUOTE_STALE_ON_ARRIVAL') AS rows_with_code
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours' AND first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
