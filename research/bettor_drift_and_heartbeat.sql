\echo ''
\echo '=== CODE-SHA DRIFT POPULATION + P_FILL HANDOVER + HEARTBEAT FACTS ==='
-- Owner directive 2026-09-19 20:0xZ. Three questions, from rows only.
--
-- THE DRIFT POPULATION HAS AN EXACT MARKER, and it is a lucky
-- consequence of how small the diff is. The ONLY executable difference
-- between the code frozen at 19:33 (POLICY_CODE_SHA 34fbb4ab) and the
-- code running at 19:58 (3e1a7fab) is one line: pFillStatus
-- NOT_ESTABLISHED -> NOT_IDENTIFIED. So the set of decisions written
-- under mismatched code is EXACTLY the set whose p_fill_status reads
-- NOT_IDENTIFIED. That identification does not depend on the boot
-- marker, on a deploy timestamp, or on any inference.

\echo ''
\echo '--- 1. THE MISMATCHED POPULATION, by its own marker ---'
SELECT 'drift|mismatched_decisions|' || count(*)::text
       || '|first=' || COALESCE(min(created_at)::text, 'NONE')
       || '|last='  || COALESCE(max(created_at)::text, 'NONE')
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW' AND p_fill_status = 'NOT_IDENTIFIED';

SELECT 'drift|matched_decisions|' || count(*)::text
       || '|first=' || COALESCE(min(created_at)::text, 'NONE')
       || '|last='  || COALESCE(max(created_at)::text, 'NONE')
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW' AND p_fill_status = 'NOT_ESTABLISHED';

-- Every mismatched row must still be NO_TRADE with the same blocker
-- vocabulary. If the changed line were load-bearing it would show HERE.
SELECT 'drift|mismatched_action|' || d.proposed_action || '|' || count(*)::text
  FROM shadow_decisions d
 WHERE d.lane = 'BETTOR_EV_SHADOW' AND d.p_fill_status = 'NOT_IDENTIFIED'
 GROUP BY d.proposed_action ORDER BY d.proposed_action;

SELECT 'drift|matched_action|' || d.proposed_action || '|' || count(*)::text
  FROM shadow_decisions d
 WHERE d.lane = 'BETTOR_EV_SHADOW' AND d.p_fill_status = 'NOT_ESTABLISHED'
 GROUP BY d.proposed_action ORDER BY d.proposed_action;

-- THE BLOCKER SETS, SIDE BY SIDE. The decisive comparison: if the two
-- populations emit the same blocker codes in the same proportion, the
-- vocabulary change touched nothing that decides anything.
WITH b AS (
    SELECT CASE WHEN d.p_fill_status = 'NOT_IDENTIFIED'
                THEN 'MISMATCHED' ELSE 'MATCHED' END AS pop,
           (x.value ->> 'code')                      AS code
      FROM shadow_decisions d,
           LATERAL jsonb_array_elements(COALESCE(d.blockers, '[]'::jsonb)) AS x
     WHERE d.lane = 'BETTOR_EV_SHADOW'
)
SELECT 'drift|blocker|' || b.pop || '|' || b.code || '|' || count(*)::text
  FROM b GROUP BY b.pop, b.code ORDER BY b.pop, b.code;

-- pBETTOR and lineage must be identical across both populations.
SELECT 'drift|p_bettor|' || COALESCE(d.p_bettor_status, 'NULL') || '|'
       || count(*)::text
  FROM shadow_decisions d
 WHERE d.lane = 'BETTOR_EV_SHADOW'
 GROUP BY d.p_bettor_status ORDER BY d.p_bettor_status;

SELECT 'drift|rn1_features_used_true|' || count(*)::text
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW' AND rn1_features_used IS NOT FALSE;

\echo ''
\echo '--- 2. P_FILL HANDOVER, RE-READ ---'
-- The confirmation the owner asked for: after the handover instant,
-- the old vocabulary must never reappear.
SELECT 'handover|P_FILL_NOT_ESTABLISHED_AFTER_HANDOVER|' || count(*)::text
  FROM shadow_decisions d
 WHERE d.lane = 'BETTOR_EV_SHADOW'
   AND d.p_fill_status = 'NOT_ESTABLISHED'
   AND d.created_at > (SELECT min(created_at) FROM shadow_decisions
                        WHERE lane = 'BETTOR_EV_SHADOW'
                          AND p_fill_status = 'NOT_IDENTIFIED');

SELECT 'handover|minutes_since_last_old_word|'
       || COALESCE(round(extract(epoch FROM (now() - max(created_at))) / 60)::text,
                   'NONE')
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW' AND p_fill_status = 'NOT_ESTABLISHED';

\echo ''
\echo '--- 3. THE HEARTBEAT, AND THE DECISION LOOP BESIDE IT ---'
-- The two planes, read separately. A working decision writer with a
-- broken health writer must be visible as exactly that.
SELECT 'hb|last_successful_heartbeat|' || h.beat_at::text
       || '|status=' || h.status
       || '|age_s=' || round(extract(epoch FROM (now() - h.beat_at)))::text
       -- WHICH KEYS the last surviving detail carries. If `pipeline` is
       -- absent from it, the last beat predates the block that broke
       -- serialization -- which is the whole hypothesis.
       || '|detail_has_pipeline='
       || (h.detail ? 'pipeline')::text
       || '|detail_keys=' || COALESCE(
             (SELECT string_agg(k, ',' ORDER BY k)
                FROM jsonb_object_keys(h.detail) AS k), 'NONE')
  FROM service_heartbeats h WHERE h.service = 'shadow_bettor';

SELECT 'hb|decision_loop_last_success|'
       || COALESCE(max(created_at)::text, 'NONE')
       || '|age_s=' || COALESCE(round(extract(epoch FROM
             (now() - max(created_at))))::text, 'NONE')
  FROM shadow_decisions WHERE lane = 'BETTOR_EV_SHADOW';

-- ATTEMPTS ARE NOT COUNTED ANYWHERE, and that is part of the finding:
-- the worker swallows the failure with log.debug. The closest honest
-- proxy is how many ticks have run since the last beat -- the loop
-- ticks every 60 s and calls heartbeat exactly once per tick.
SELECT 'hb|ticks_since_last_beat_approx|'
       || round(extract(epoch FROM (now() - h.beat_at)) / 60)::text
  FROM service_heartbeats h WHERE h.service = 'shadow_bettor';

-- The control: other services using the SAME heartbeat() are fresh, so
-- the function works and the payload is what differs.
SELECT 'hb|control|' || h.service || '|' || h.status || '|age_s='
       || round(extract(epoch FROM (now() - h.beat_at)))::text
  FROM service_heartbeats h
 WHERE h.service IN ('chain_listener', 'shadow_rn1', 'poller')
 ORDER BY h.service;
