\echo == INGEST GAP: IS RN1 QUIET, OR IS DETECTION STOPPED ==
\echo
\echo Probe 2 observed: nothing has been WRITTEN to trades since
\echo 17:16:33Z and RN1s newest fill is 17:01:06Z, while chain_listener
\echo and poller both beat ok seconds before the probe ran.
\echo
\echo Probe 2 tried to separate those by comparing RN1 against other
\echo wallets. That comparison is WEAK HERE and I should not lean on it:
\echo the roster was cut to RN1 alone on 2026-09-05, so any_whale and
\echo rn1 are very nearly the same population. This probe separates them
\echo a different way -- by the shape of the writing over time, and by
\echo the listener counters that advance only when it decodes something.
\echo

\echo -- 1. WHEN DID WRITING STOP, to the ten minutes ------------------
SELECT 'write_bucket',
       to_char(date_trunc('hour', detected_at)
               + floor(EXTRACT(MINUTE FROM detected_at) / 10)
                 * interval '10 minutes', 'YYYY-MM-DD HH24:MI'),
       count(*)::text
  FROM trades
 WHERE detected_at > now() - interval '5 hours'
 GROUP BY 2
 ORDER BY 2 DESC;

\echo -- 2. AND WHEN DID THE FILLS THEMSELVES STOP ---------------------
\echo detected_at is OUR clock, ts is the venues. If ts keeps going and
\echo detected_at does not, we stopped. If both stop together, he did.
SELECT 'fill_bucket',
       to_char(date_trunc('hour', ts)
               + floor(EXTRACT(MINUTE FROM ts) / 10)
                 * interval '10 minutes', 'YYYY-MM-DD HH24:MI'),
       count(*)::text
  FROM trades
 WHERE ts > now() - interval '5 hours'
 GROUP BY 2
 ORDER BY 2 DESC;

\echo -- 3. THE LISTENER COUNTERS RIGHT NOW ----------------------------
\echo These advance only when the lane actually decodes something. Read
\echo twice, minutes apart, and the pair answers the question outright.
SELECT 'counter', service, status, beat_at::text,
       round(EXTRACT(EPOCH FROM (now() - beat_at)))::text,
       detail::text
  FROM service_heartbeats
 WHERE service IN ('chain_listener', 'poller', 'shadow_rn1')
 ORDER BY service;

\echo -- 4. THE ROSTER, so the population is stated not assumed --------
SELECT 'roster', w.username, w.active::text, w.pinned::text,
       count(t.id)::text
  FROM whales w
  LEFT JOIN trades t ON t.whale_id = w.id
                    AND t.detected_at > now() - interval '24 hours'
 WHERE w.active IS TRUE
 GROUP BY w.username, w.active, w.pinned
 ORDER BY count(t.id) DESC
 LIMIT 12;

\echo -- 5. THE SHADOW COUNTS AGAIN, cheap and current -----------------
SELECT 'shadow_now', 'observations', count(*)::text FROM rn1_observations
 UNION ALL
SELECT 'shadow_now', 'decisions', count(*)::text FROM shadow_decisions
 UNION ALL
SELECT 'shadow_now', 'market_states', count(*)::text
  FROM shadow_market_states
 UNION ALL
SELECT 'shadow_now', 'executions', count(*)::text FROM shadow_executions;

\echo -- 6. THE FROZEN POLICY, echoed for the record -------------------
SELECT 'policy', policy_version, policy_sha, frozen_at::text
  FROM shadow_policy_versions;

\echo == END ==
