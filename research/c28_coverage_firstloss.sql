-- c28 COVERAGE FIRST-LOSS CHECK (read-only), Sunday 2026-10-04.
-- Follows c28_coverage_receipt.sql (run 37198700093). For every league the
-- funnel shows with provider events > 0 and a downstream zero today, what
-- the events actually were (commence time vs the ET day, furthest raw
-- ledger stage, refusals), so the incident's FIRST LOSS can be checked
-- against the rows, plus Derek-only verdicts per league and the snapshot
-- rows behind the 10-02 / 10-03 alerts.
--   ET day = [2026-10-04 04:00Z, 2026-10-05 04:00Z)
\echo '== F0 · read instant =='
SELECT now() AS read_at;

\echo '== F1 · NCAAF provider events seen in ET-day cycles: commence, furthest raw stage, slug, refusals =='
SELECT provider_event_id, max(away) || ' at ' || max(home) AS fixture,
       max(commence_time) AS commence_time,
       CASE WHEN max(commence_time) ~ '^[0-9]{4}-' THEN
            to_char(max(commence_time)::timestamptz AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI') END AS commence_et,
       max(us_market_slug) AS slug,
       max(CASE WHEN outcome IN ('ADMITTED', 'ALREADY_RECORDED') THEN 99
                WHEN outcome = 'REFUSED' AND stage ~ '^[1-8]_' THEN substr(stage, 1, 1)::int
                ELSE 0 END) AS raw_stage,
       count(*) AS rows_, min(cycle_at) AS first_cycle, max(cycle_at) AS last_cycle,
       (SELECT jsonb_object_agg(k, n) FROM (
            SELECT coalesce(o2.stage, '') || ':' || coalesce(o2.first_refusal, o2.outcome) AS k, count(*) AS n
              FROM ext_candidate_outcomes o2
             WHERE o2.provider_event_id = o.provider_event_id AND o2.sport_key = 'americanfootball_ncaaf'
               AND o2.cycle_at >= timestamptz '2026-10-04 04:00Z' AND o2.cycle_at < timestamptz '2026-10-05 04:00Z'
             GROUP BY 1) t) AS outcomes
  FROM ext_candidate_outcomes o
 WHERE sport_key = 'americanfootball_ncaaf' AND provider_event_id IS NOT NULL
   AND cycle_at >= timestamptz '2026-10-04 04:00Z' AND cycle_at < timestamptz '2026-10-05 04:00Z'
 GROUP BY provider_event_id
 ORDER BY max(us_market_slug) NULLS LAST, max(commence_time);

\echo '== F2 · NCAAF valuations and Derek decisions with decided_at in the ET day =='
SELECT ev.id, ev.us_market_slug, ev.event_key, ev.record_purpose, ev.decided_at,
       left(array_to_string(ev.refusals, ','), 200) AS refusals
  FROM external_valuations ev
 WHERE ev.decided_at >= timestamptz '2026-10-03 04:00Z'
   AND (ev.us_market_slug LIKE '%-cfb-%' OR ev.event_key IN (
        SELECT provider_event_id FROM ext_candidate_outcomes
         WHERE sport_key = 'americanfootball_ncaaf' AND cycle_at >= timestamptz '2026-10-03 04:00Z'))
 ORDER BY ev.decided_at DESC LIMIT 40;

\echo '== F3 · Derek-only verdicts per league token, ET day =='
SELECT lower(split_part(coalesce(us_market_slug, ''), '-', 2)) AS token,
       count(DISTINCT us_market_slug) AS contracts_decided,
       count(DISTINCT us_market_slug) FILTER (WHERE verdict = 'ENTER') AS contracts_enter,
       (array_agg(DISTINCT refusal) FILTER (WHERE refusal IS NOT NULL))[1:5] AS refusals
  FROM paper_decisions
 WHERE strategy = 'DEREK_ENTRY_POLICY_V2'
   AND decided_at >= timestamptz '2026-10-04 04:00Z' AND decided_at < timestamptz '2026-10-05 04:00Z'
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== F4 · paper orders / fills per league token and strategy, ET day =='
SELECT lower(split_part(o.us_market_slug, '-', 2)) AS token, pd.strategy, o.role,
       count(DISTINCT o.order_id) AS orders,
       count(DISTINCT f.fill_id) AS fills
  FROM paper_orders o
  LEFT JOIN paper_decisions pd ON pd.decision_id = o.decision_id
  LEFT JOIN paper_fills f ON f.order_id = o.order_id
 WHERE o.created_at >= timestamptz '2026-10-04 04:00Z' AND o.created_at < timestamptz '2026-10-05 04:00Z'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== F5 · snapshots behind the 10-02 / 10-03 alerts (ET) =='
SELECT day, league, provider_events AS prov, normalized_events AS norm, venue_discovered AS disc,
       mapped_events AS mapped, settlement_supported AS settle, evaluated_events AS eval,
       decided_events AS decided, entered_events AS entered, final,
       to_char(computed_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS computed_utc
  FROM coverage_funnel_snapshots
 WHERE tz = 'America/New_York' AND day IN (date '2026-10-02', date '2026-10-03')
   AND provider_events > 0
 ORDER BY day, league;

\echo '== F6 · the NCAAF 10-04 alert row in full =='
SELECT alert_id, stage_from, stage_to, severity, detail::text AS detail, audrey_finding_id,
       detected_at, recorded_at
  FROM coverage_collapse_alerts
 WHERE day = date '2026-10-04';
