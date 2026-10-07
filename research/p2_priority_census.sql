-- READ-ONLY. Every PRIORITY member (registry priority <= 10: open paper
-- positions + evaluated candidates) classified on the stored evidence:
-- refdata (PMX listed / unlisted / pending), stream shard assigned, latest
-- REST/public book age, latest provider valuation age, identity / coverage /
-- settlement state, event start. (Stream currency itself is in-process and
-- comes from the snapshot's tier counts.)
WITH pm AS (
  SELECT r.contract_id, r.priority, r.required_reason, r.sport, r.family, r.period,
         r.event_start, r.subscription_shard, r.desired_subscription,
         CASE WHEN r.refdata IS NULL THEN 'REFDATA_PENDING'
              WHEN r.refdata->>'unlisted'='true' THEN 'PMX_UNLISTED'
              ELSE 'PMX_LISTED' END refdata_state,
         r.coverage_state, r.coverage_why, r.settlement_state
    FROM market_plane_registry r WHERE r.active AND r.priority <= 10),
bk AS (
  SELECT us_market_slug, max(observed_at) at FROM paper_book_observations
   WHERE us_market_slug IN (SELECT contract_id FROM pm)
     AND observed_at > now() - interval '6 hours' GROUP BY 1),
va AS (
  SELECT us_market_slug, max(observed_at) at FROM external_valuations
   WHERE us_market_slug IN (SELECT contract_id FROM pm)
     AND decided_at > now() - interval '6 hours' GROUP BY 1)
SELECT pm.priority, pm.refdata_state, (pm.subscription_shard IS NOT NULL) shard_assigned,
       CASE WHEN bk.at IS NULL THEN 'NO_BOOK_6H'
            WHEN bk.at > now()-interval '300 seconds' THEN 'REST_CURRENT'
            ELSE 'REST_STALE' END rest,
       CASE WHEN pm.event_start < now() - interval '4 hours' THEN 'STARTED_GT_4H'
            WHEN pm.event_start < now() THEN 'IN_PLAY_OR_RECENT'
            WHEN pm.event_start IS NULL THEN 'NO_START'
            ELSE 'PREGAME' END phase,
       pm.coverage_state, count(*) n,
       count(*) FILTER (WHERE va.at > now()-interval '30 seconds') prob_current
  FROM pm LEFT JOIN bk ON bk.us_market_slug=pm.contract_id
          LEFT JOIN va ON va.us_market_slug=pm.contract_id
 GROUP BY 1,2,3,4,5,6 ORDER BY 1,7 DESC;
-- the not-REST-current held contracts, individually
SELECT pm.contract_id, pm.required_reason, pm.refdata_state, pm.subscription_shard,
       round(extract(epoch FROM now()-bk.at)) book_age_s, pm.event_start, pm.coverage_state
  FROM pm LEFT JOIN bk ON bk.us_market_slug=pm.contract_id
 WHERE pm.priority = 0 AND (bk.at IS NULL OR bk.at < now()-interval '300 seconds')
 ORDER BY pm.event_start NULLS LAST LIMIT 130;
