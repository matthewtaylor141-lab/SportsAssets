\echo == SHADOW FIRST CHAIN ==
\echo
\echo Probe 1 established that 068/069/070 are applied, every append-only
\echo trigger and constraint is present, the partial dedupe index exists,
\echo lane is NOT NULL with NO_DEFAULT, and shadow_rn1 beats healthy with
\echo storeReady true and the policy FROZEN.
\echo
\echo This probe answers the rest: how many rows exist, what the first
\echo chain looks like, and -- the question probe 1 raised -- whether RN1
\echo is QUIET or our ingestion has STOPPED. Those look identical from a
\echo count of zero and mean opposite things.
\echo

\echo -- A. THE FROZEN POLICY ROW ---------------------------------------
SELECT 'policy', policy_version, lane, policy_sha, policy_code_sha,
       frozen_at::text, sizing_policy_version, latency_policy_version
  FROM shadow_policy_versions
 ORDER BY frozen_at;

\echo -- B. THE COUNTS --------------------------------------------------
SELECT 'count_observations',
       count(*) FILTER (WHERE record_kind = 'OBSERVATION')::text,
       count(*) FILTER (WHERE record_kind = 'OBSERVATION_INVALIDATED')::text,
       count(*) FILTER (WHERE record_kind = 'OBSERVATION_CORRECTED')::text
  FROM rn1_observations;

SELECT 'count_decisions', lane, proposed_action, count(*)::text
  FROM shadow_decisions
 GROUP BY lane, proposed_action
 ORDER BY lane, proposed_action;

SELECT 'count_other', 'market_states', count(*)::text
  FROM shadow_market_states
 UNION ALL
SELECT 'count_other', 'executions', count(*)::text FROM shadow_executions
 UNION ALL
SELECT 'count_other', 'positions', count(*)::text FROM shadow_positions
 UNION ALL
SELECT 'count_other', 'scores', count(*)::text FROM shadow_scores
 UNION ALL
SELECT 'count_other', 'disagreements', count(*)::text
  FROM shadow_disagreements;

\echo -- C. IS RN1 QUIET, OR IS INGESTION STOPPED -----------------------
\echo The decisive comparison. If OTHER wallets are still landing fills
\echo while RN1 is not, the pipe is alive and RN1 is simply not acting.
\echo If NOTHING is landing, the silence is ours and the zero above is
\echo not evidence about RN1 at all.
SELECT 'ingest_alive', 'any_whale_last_fill', max(ts)::text,
       round(EXTRACT(EPOCH FROM (now() - max(ts))))::text
  FROM trades;

SELECT 'ingest_alive', 'any_whale_last_write', max(detected_at)::text,
       round(EXTRACT(EPOCH FROM (now() - max(detected_at))))::text
  FROM trades;

SELECT 'ingest_rate', date_trunc('hour', detected_at)::text,
       count(*)::text
  FROM trades
 WHERE detected_at > now() - interval '6 hours'
 GROUP BY 1
 ORDER BY 1 DESC;

\echo -- D. RN1 SPECIFICALLY, BY THE HOUR WE WROTE IT -------------------
SELECT 'rn1_by_hour', date_trunc('hour', t.detected_at)::text,
       count(*)::text
  FROM trades t
  JOIN whales w ON w.id = t.whale_id
 WHERE lower(w.username) = 'rn1'
   AND t.detected_at > now() - interval '12 hours'
 GROUP BY 1
 ORDER BY 1 DESC;

\echo -- E. THE FIRST CHAIN, if any row exists --------------------------
SELECT 'first_observation', o.rn1_observation_id, o.record_kind,
       o.idempotency_basis, o.source_type, o.source_reference,
       o.rn1_source_ts::text, o.bettor_received_ts::text,
       o.observation_written_ts::text, o.source_ts_status,
       COALESCE(o.event_id, 'NULL'), COALESCE(o.market_id, 'NULL'),
       COALESCE(o.symbol, 'NULL'), COALESCE(o.outcome_leg, 'NULL'),
       o.side, o.rn1_price::text, o.rn1_quantity::text, o.ingest_version
  FROM rn1_observations o
 ORDER BY o.bettor_received_ts
 LIMIT 3;

SELECT 'first_decision', d.shadow_decision_id, d.lane, d.policy_version,
       d.model_version, d.evidence_source, d.proposed_action,
       COALESCE(d.proposed_side, 'NULL'),
       COALESCE(d.proposed_price::text, 'NULL'),
       COALESCE(d.proposed_quantity::text, 'NULL'),
       COALESCE(d.rn1_price::text, 'NULL'),
       COALESCE(d.price_when_bettor_observed::text, 'NULL'),
       COALESCE(d.price_when_bettor_decided::text, 'NULL'),
       d.decision_ts::text, d.bettor_received_ts::text,
       COALESCE(d.p_bettor::text, 'NULL') AS p_bettor,
       COALESCE(d.p_bettor_status, 'NULL'),
       COALESCE(d.information_ev::text, 'NULL') AS information_ev,
       d.blockers::text, d.reason_codes::text,
       COALESCE(d.rn1_observation_id, 'NULL'),
       COALESCE(d.market_state_id, 'NULL')
  FROM shadow_decisions d
 ORDER BY d.decision_ts
 LIMIT 3;

\echo -- F. THE DEDUPE, OBSERVED ---------------------------------------
\echo Sightings whose key appears more than once would be the dedupe
\echo failing. Zero rows here is the index doing its job.
SELECT 'dupe_key', idempotency_key, count(*)::text
  FROM rn1_observations
 WHERE record_kind = 'OBSERVATION'
 GROUP BY idempotency_key
HAVING count(*) > 1
 LIMIT 5;

\echo == END OF FIRST CHAIN ==
