-- RC6.2 lane S (SETTLEMENT-PROOF REPAIRS), BEFORE readback. SELECT only.
-- The production lineage of the settlement-unproven active contracts, read
-- from the plane's own registry with NO top-40 cut, so a local replay of the
-- repaired classifier can state its expected after-counts per class:
--   S0  population by venue and settlement state, code-version markers
--   S1  the lineage report's full split, verbatim:
--         venue, settlement_state, split_part(why,':',1),
--         split_part(why,':',2), sport, family, period, market_type, count
--   S2  the latest SNAPSHOT event: how many settlement reasons its
--       top_reasons carries, and what they sum to against the population
--   S3  the replay census: the inputs market_plane.settlement.state_for
--       reads for the fall-through (sport, family, period, market type,
--       ontology gaps, event id held) with the full why, and whether the why
--       IS the BOOKMAKER_TERMS_NOT_HELD:<sport>/<family>/<period> fall-through
--   S4  the regime-matrix join (revenue_reliability.evidence segments): per
--       sport x family x regime, evaluations and how many sit on a registry
--       row in EACH settlement state (the reader counted COMPATIBLE only)
--   S5  decided money lines whose recorded verdict is UNKNOWN: the recorded
--       comparison's unstated / mismatched conditions and blockers
-- No write, no secret.
\echo === S0. population by venue x settlement state, and RC6.1 markers ===
SELECT venue, coalesce(settlement_state, '<null>') AS settlement_state,
       count(*) AS active,
       count(*) FILTER (WHERE settlement_why LIKE 'LINE_%'
                           OR settlement_why LIKE 'BOOK_TERMS_SCOPE%')
         AS rc61_whys,
       max(settlement_at) AS last_settlement_at, now() AS read_at
  FROM market_plane_registry WHERE active GROUP BY 1, 2 ORDER BY 1, 2;
\echo === S1. the report full split: [venue, state, why1, why2, sport, family, period, market_type, n] x 40 per line ===
WITH k AS (
  SELECT venue, settlement_state, split_part(settlement_why, ':', 1) AS w1,
         split_part(settlement_why, ':', 2) AS w2, sport, family, period,
         market_type, count(*) AS n
    FROM market_plane_registry WHERE active
   GROUP BY 1, 2, 3, 4, 5, 6, 7, 8),
b AS (SELECT k.*, (row_number() OVER (ORDER BY n DESC, venue, settlement_state,
                                      w1, w2, sport, family, period,
                                      market_type) - 1) / 40 AS grp FROM k)
SELECT json_agg(json_build_array(venue, settlement_state, w1, w2, sport,
                                 family, period, market_type, n)
                ORDER BY n DESC, venue, settlement_state, w1, w2, sport,
                         family, period, market_type)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
\echo === S2. latest SNAPSHOT: settlement top_reasons keys and sum vs population ===
WITH s AS (
  SELECT at, payload -> 'coverage' AS cov
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > now() - interval '6 hours'
   ORDER BY at DESC LIMIT 1)
SELECT at, (cov ->> 'active')::bigint AS active,
       (SELECT count(*) FROM jsonb_object_keys(cov -> 'settlement' -> 'top_reasons'))
         AS settlement_reason_keys,
       (SELECT sum(value::bigint) FROM jsonb_each_text(cov -> 'settlement' -> 'top_reasons'))
         AS settlement_reason_sum,
       (SELECT count(*) FROM jsonb_object_keys(cov -> 'top_reasons'))
         AS coverage_reason_keys,
       (SELECT sum(value::bigint) FROM jsonb_each_text(cov -> 'top_reasons'))
         AS coverage_reason_sum,
       (cov -> 'settlement' -> 'by_state')::text AS settlement_by_state
  FROM s;
\echo === S3. replay census: [venue, state, basis, why(200), sport, family, period, market_type, gaps, event_held, fallthrough, n] x 25 per line ===
WITH k AS (
  SELECT venue, coalesce(settlement_state, '<null>') AS st,
         coalesce(settlement_basis, '<null>') AS basis,
         left(coalesce(settlement_why, '<null>'), 200) AS why,
         sport, family, period, market_type,
         coalesce((ontology -> 'gaps')::text, '<null>') AS gaps,
         (event_id IS NOT NULL AND event_id <> '') AS event_held,
         (settlement_why = 'BOOKMAKER_TERMS_NOT_HELD:'
            || coalesce(sport, 'UNKNOWN_SPORT') || '/'
            || coalesce(family, 'UNKNOWN_FAMILY') || '/'
            || coalesce(period, 'UNKNOWN_PERIOD')) AS fallthrough,
         count(*) AS n
    FROM market_plane_registry WHERE active
   GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
b AS (SELECT k.*, (row_number() OVER (ORDER BY n DESC, venue, st, why, sport,
                                      family, period, market_type, gaps)
                   - 1) / 25 AS grp FROM k)
SELECT json_agg(json_build_array(venue, st, basis, why, sport, family, period,
                                 market_type, gaps, event_held, fallthrough, n)
                ORDER BY n DESC, venue, st, why, sport, family, period,
                         market_type, gaps)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
\echo === S4. regime-matrix join (90 d, paper_acct_main): evaluations by registry settlement state ===
SELECT coalesce(e.sport, 'UNKNOWN') AS sport,
       coalesce(e.market_family, 'UNKNOWN') AS family,
       coalesce(e.regime, 'UNKNOWN') AS regime, count(*) AS evaluations,
       count(r.contract_id) AS in_registry,
       count(*) FILTER (WHERE r.settlement_state = 'SETTLEMENT_PROVEN_COMPATIBLE')
         AS proven_compatible,
       count(*) FILTER (WHERE r.settlement_state = 'SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED')
         AS proven_priced,
       count(*) FILTER (WHERE r.settlement_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN')
         AS not_proven,
       count(*) FILTER (WHERE r.contract_id IS NOT NULL
                          AND r.settlement_state IS NULL) AS no_state,
       count(DISTINCT e.us_market_slug) AS contracts
  FROM paper_profitability_evaluations e
  LEFT JOIN market_plane_registry r ON r.contract_id = e.us_market_slug
 WHERE e.account_id = 'paper_acct_main'
   AND e.evaluated_at > now() - make_interval(days => 90)
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3 LIMIT 2000;
\echo === S4b. registry key uniqueness (the join must not duplicate an evaluation) ===
SELECT count(*) AS rows, count(DISTINCT contract_id) AS contract_ids
  FROM market_plane_registry;
\echo === S5. decided (24 h) money lines with recorded verdict UNKNOWN: what the record says ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug, sport_family,
         market, settlement_comparison::jsonb AS sc
    FROM external_valuations
   WHERE decided_at > now() - interval '24 hours'
     AND us_market_slug IS NOT NULL
   ORDER BY us_market_slug, decided_at DESC, id DESC)
SELECT v.sport_family, v.market,
       coalesce(v.sc ->> 'verdict', v.sc ->> 'status', v.sc ->> 'compatibility')
         AS verdict,
       (v.sc -> 'unstated_conditions')::text AS unstated,
       (v.sc -> 'mismatched_conditions')::text AS mismatched,
       left((v.sc -> 'blockers')::text, 220) AS blockers,
       (v.sc -> 'book_capture') IS NOT NULL
         AND v.sc -> 'book_capture' <> 'null'::jsonb AS book_capture_held,
       count(*) AS n
  FROM v JOIN market_plane_registry g ON g.contract_id = v.slug AND g.active
 WHERE coalesce(v.sc ->> 'verdict', v.sc ->> 'status', v.sc ->> 'compatibility')
       = 'UNKNOWN'
 GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY n DESC LIMIT 60;
