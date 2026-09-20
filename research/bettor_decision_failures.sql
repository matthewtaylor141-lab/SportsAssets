-- §20: THE LOST PROSPECTIVE DECISION WRITES, AND WHETHER THEY STOPPED.
--
-- shadow_decisions.policy_version carries a FOREIGN KEY into
-- shadow_policy_versions, so a decision written under a policy that is
-- not frozen cannot be inserted at all. That is the ledger working as
-- designed -- the alternative is a prospective record whose rules can
-- drift -- but it means any gap between a version bump and its freeze
-- costs real rows.
--
-- The worker's fix is decision_writing_allowed starting FALSE and only
-- turning TRUE after freeze_policy succeeds AND the code sha verifies.
-- This measures whether that actually held.

-- L1. Every failure, by stage and error class, oldest and newest.
SELECT 'L1_BY_STAGE' AS section,
       stage,
       error_class,
       count(*)        AS failures,
       min(failed_at)  AS first_seen,
       max(failed_at)  AS last_seen
  FROM bettor_decision_failures
 GROUP BY 2, 3
 ORDER BY failures DESC;

-- L2. The FK violations specifically, by day, so a fix shows as a stop
-- rather than as a smaller number.
SELECT 'L2_FK_BY_DAY' AS section,
       date_trunc('day', failed_at) AS day,
       count(*) AS fk_failures
  FROM bettor_decision_failures
 WHERE stage = 'DECISION_WRITE'
   AND error_class ILIKE '%ForeignKey%'
 GROUP BY 2
 ORDER BY 2;

-- L3. The most recent, verbatim -- the database's own words.
SELECT 'L3_LATEST' AS section,
       failed_at,
       stage,
       error_class,
       left(error_text, 180) AS error_text
  FROM bettor_decision_failures
 ORDER BY failed_at DESC
 LIMIT 5;

-- L4. Since the V5 freeze: any at all is a defect in the guard.
SELECT 'L4_SINCE_V5' AS section,
       count(*) FILTER (WHERE stage = 'DECISION_WRITE'
                          AND error_class ILIKE '%ForeignKey%')
           AS fk_failures_since_v5,
       count(*) AS all_failures_since_v5
  FROM bettor_decision_failures
 WHERE failed_at >= (SELECT min(frozen_at)
                       FROM shadow_policy_versions
                      WHERE policy_version = 'BETTOR_EV_SHADOW_V5');
