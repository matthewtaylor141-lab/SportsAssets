-- Read-only: the execution intent(s) marked live_eligible without a
-- LIVE_ADMISSIBLE admission verdict (actual-lane guard K9 / G).
SELECT intent_id, decision_id, created_at, decided_at, strategy, policy_version, us_market_slug, order_intent, live_eligible, live_qty, actual_mirror_id,
       actual_state, actual_refusal,
       live_eligibility->'admission'->>'verdict' AS admission_verdict,
       left((live_eligibility)::text, 1500) AS live_eligibility
  FROM execution_intents
 WHERE live_eligible AND coalesce(live_eligibility->'admission'->>'verdict', '') <> 'LIVE_ADMISSIBLE';
SELECT count(*) FILTER (WHERE live_eligible) AS live_eligible_total,
       count(*) FILTER (WHERE live_eligible AND created_at > now() - interval '3 hours') AS live_eligible_3h,
       min(created_at) FILTER (WHERE live_eligible) AS first_live_eligible,
       max(created_at) FILTER (WHERE live_eligible) AS last_live_eligible
  FROM execution_intents;
SELECT count(*) AS mirror_orders_from_intents FROM execmirror_orders WHERE execution_intent_id IS NOT NULL;
