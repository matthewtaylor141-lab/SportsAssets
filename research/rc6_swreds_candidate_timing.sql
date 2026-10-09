-- READ-ONLY. RC6 lane C (software reds): where one metered fetch's 30 s window
-- goes, candidate by candidate. Every valuation the deciding cycle sealed in
-- the first minutes of four metered cycles (17:04Z and 18:39Z with the PinnAPI
-- feed up, 20:09Z and 23:09Z with it refused), in decision order, with the
-- clocks the record itself carries: the provider quote's instants, our venue
-- read instant, the decision instant and the lag between them (book read +
-- rules read + fixture read), and whether the fixture scope was fetched this
-- cycle. SELECT only.

\echo C1 the valuations of four metered cycles, in decision order
WITH cyc(label, t0) AS (VALUES
        ('17:04', timestamptz '2026-10-08 17:04:30+00'),
        ('18:39', timestamptz '2026-10-08 18:39:44+00'),
        ('20:09', timestamptz '2026-10-08 20:09:46+00'),
        ('23:09', timestamptz '2026-10-08 23:09:48+00'))
SELECT c.label,
       round(extract(epoch FROM v.decided_at - c.t0)::numeric, 2) AS dec_s,
       left(v.sport_family, 8) AS fam, v.record_purpose AS purpose,
       left(v.us_market_slug, 30) AS slug, left(v.buy_intent, 22) AS intent,
       round(v.age_s::numeric, 1) AS age_s,
       round(extract(epoch FROM v.received_at - v.observed_at)::numeric, 1) AS lag_s,
       round(((v.calibration_only_evidence->>'decision_lag_s')::float8)::numeric, 2) AS dlag_s,
       round(((v.calibration_only_evidence->'displayed_quote'->>'read_at')::float8
              - extract(epoch FROM c.t0))::numeric, 2) AS read_s,
       v.settlement_comparison->'fixture_acquisition'->>'attempted' AS fx_try,
       left(v.settlement_comparison->'fixture_acquisition'->>'refusal', 26) AS fx_ref,
       left(v.settlement_comparison->>'venue_rules_source', 18) AS rules_src,
       left(v.refusals[1], 34) AS r1, left(v.refusals[2], 30) AS r2
  FROM cyc c JOIN external_valuations v
    ON v.decided_at >= c.t0 AND v.decided_at < c.t0 + interval '150 seconds'
 WHERE v.record_purpose IN ('ENTRY_DECISION', 'CALIBRATION_ONLY')
 ORDER BY c.label, v.decided_at
 LIMIT 220;

\echo C2 decision lag (book read to decision) of every calibration-only valuation per hour
SELECT date_trunc('hour', v.decided_at) AS hour, v.sport_family,
       count(*) AS n,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY (v.calibration_only_evidence->>'decision_lag_s')::float8)::numeric, 2) AS dlag_p50,
       round(percentile_disc(0.9) WITHIN GROUP (ORDER BY (v.calibration_only_evidence->>'decision_lag_s')::float8)::numeric, 2) AS dlag_p90,
       round(max((v.calibration_only_evidence->>'decision_lag_s')::float8)::numeric, 2) AS dlag_max,
       count(*) FILTER (WHERE v.settlement_comparison->'fixture_acquisition'->>'attempted' = 'true') AS fx_fetched,
       count(*) FILTER (WHERE 'QUOTE_STALE' = ANY (v.refusals)) AS quote_stale_at_decision
  FROM external_valuations v
 WHERE v.decided_at >= timestamptz '2026-10-08 16:00:00+00'
   AND v.record_purpose = 'CALIBRATION_ONLY'
 GROUP BY 1, 2 ORDER BY 1, 2;
