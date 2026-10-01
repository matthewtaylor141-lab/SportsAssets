-- READ-ONLY. WHY EVERY PINNACLE_ONLY_PAPER_BENCHMARK DECISION FAILS THE
-- SETTLEMENT-TERMS CHECK, AND HOW MANY VALUATIONS REACH THE BENCHMARK AT ALL.
--
-- paper_benchmark.contract_match passes settlement only when the row's
-- settlement_comparison says COMPATIBLE and no settlement-stage refusal is
-- on the row. This prints, per benchmark decision, the stored comparison and
-- the lane's settlement-stage codes, then the same over every valuation of
-- the last 24 h (by sport family and market), then the full per-provider
-- funnel of the latest collection cycle. Nothing here writes.
--
-- S1 benchmark decisions: the settlement check's own detail
-- S2 the stored settlement_comparison on those valuations (shortened)
-- S3 last 24 h valuations: compatibility x sport family x market x purpose
-- S4 last 24 h valuations: refusal codes, counted
-- S5 latest cycle funnel, one row per provider/sport

\echo '== S1 · benchmark decisions: the settlement check =='
SELECT d.decided_at, d.valuation_id, left(d.us_market_slug, 34) AS slug,
       c->>'detail' AS settlement_detail,
       c->>'compatibility' AS compatibility,
       c->>'overall_established' AS overall_established,
       c->'lane_refusals' AS lane_settlement_refusals
  FROM paper_decisions d,
       jsonb_array_elements(d.pinnacle->'contract_match'->'checks') c
 WHERE d.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
   AND c->>'refusal' = 'SETTLEMENT_NOT_SUPPORTED'
 ORDER BY d.decided_at DESC LIMIT 20;

\echo '== S2 · stored settlement_comparison on those valuations =='
SELECT v.id, v.sport_family, v.market, v.period, v.record_purpose,
       v.settlement_comparison->>'compatibility' AS compatibility,
       left(v.settlement_comparison::text, 900) AS comparison
  FROM external_valuations v
 WHERE v.id IN (SELECT DISTINCT valuation_id FROM paper_decisions
                 WHERE strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK')
 ORDER BY v.id DESC LIMIT 12;

\echo '== S3 · last 24 h valuations by compatibility, family, market, purpose =='
SELECT v.sport_family, v.market, v.record_purpose,
       COALESCE(v.settlement_comparison->>'compatibility', 'NULL') AS compatibility,
       count(*) AS valuations, count(DISTINCT v.us_market_slug) AS markets,
       max(v.decided_at) AS latest
  FROM external_valuations v
 WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND v.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC;

\echo '== S4 · last 24 h valuations: refusal codes =='
SELECT r AS refusal, count(*) AS valuations,
       count(DISTINCT v.us_market_slug) AS markets
  FROM external_valuations v, unnest(v.refusals) r
 WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND v.decided_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== S5 · latest collection cycle funnel per provider/sport =='
SELECT f.key AS provider_sport,
       f.value->>'provider_events' AS provider_events,
       f.value->>'identity_resolved' AS identity_resolved,
       f.value->>'with_pinnacle_h2h' AS with_pinnacle_h2h,
       f.value->>'mapped_to_a_venue_contract' AS mapped,
       f.value->>'venue_markets_open_and_fresh' AS venue_open_fresh,
       f.value->>'recorded_for_calibration_only' AS calibration_only,
       f.value->>'written' AS written,
       f.value->'refusals' AS refusals
  FROM ingestion_state s, jsonb_each(s.value->'funnel_by_provider_sport') f
 WHERE s.key = 'ext_pinnacle_last_cycle'
 ORDER BY (f.value->>'provider_events')::int DESC NULLS LAST;
