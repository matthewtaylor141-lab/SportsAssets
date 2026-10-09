-- READ-ONLY. RC6 lane redteam-scenarios, second pass: why the twin's
-- KAREN_BLOCK_ACCEPTED world never scores a blocked position in both worlds
-- (KAREN_VALUE). twin.reads.karen_blocks reads karen_challenges WHERE
-- blocked; twin.engine.world_karen matches a blocked challenge to a PAPER
-- position only by target_id or an evidence_refs id equal to the position's
-- decision_id or group_id. Every statement is a SELECT.

\echo B1 karen_challenges by blocked x target_agent x target_kind x state
SELECT blocked, target_agent, target_kind, state, count(*) AS n,
       to_char(max(challenged_at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI') AS newest
  FROM karen_challenges
 GROUP BY 1, 2, 3, 4 ORDER BY blocked DESC, n DESC;

\echo B2 blocked challenges whose target_id or an evidence_refs id is a PAPER order decision_id or group_id (the only join world_karen uses)
WITH bc AS (
  SELECT challenge_id, target_id,
         ARRAY(SELECT x->>'id' FROM jsonb_array_elements(
                 CASE WHEN jsonb_typeof(evidence_refs) = 'array'
                      THEN evidence_refs ELSE '[]'::jsonb END) x) AS ref_ids
    FROM karen_challenges WHERE blocked)
SELECT count(*) AS blocked_challenges,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM paper_orders o
          WHERE o.decision_id = bc.target_id OR o.group_id = bc.target_id
             OR o.decision_id = ANY (bc.ref_ids) OR o.group_id = ANY (bc.ref_ids))) AS matching_a_paper_position
  FROM bc;

\echo B3 detectors of blocked challenges
SELECT detector, target_kind, count(*) AS n
  FROM karen_challenges WHERE blocked GROUP BY 1, 2 ORDER BY n DESC LIMIT 20;
