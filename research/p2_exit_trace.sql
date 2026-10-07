-- READ-ONLY. End-to-end trace of the three complete-packet EXIT reviews that
-- took CANCEL_STANDING_BEFORE_EXIT after the 10af406/cd8a449 boots.
WITH g(group_id) AS (VALUES ('paperexpgrp:2ed183f135ccf7eb84c3a94c'),
  ('paperexpgrp:c077f868dc8742c4d3d5ee15'),('paperexpgrp:c3da878fb6e90c4ec771a8f6'))
SELECT r.group_id, r.reviewed_at, r.trigger, r.recommendation, r.refusal,
       coalesce(r.measure->>'probability_source', r.measure->>'source') src,
       r.measure->>'evidence_state' ev,
       round((r.measure->>'probability_age_s')::numeric,1) age_s,
       r.measure->'feed'->>'reason' feed_reason,
       r.measure->'exit_walk'->>'fresh' walk_fresh,
       r.measure->'management_packet'->>'missing' missing,
       r.measure->'exit_continuation' cont,
       r.selection->>'mechanical_selection' sel,
       r.action->>'taken' taken, r.action->'orders' orders, r.action->>'why' why,
       jsonb_array_length(coalesce(r.standing->'live_orders','[]')) n_standing
  FROM paper_xavier_reviews r JOIN g USING (group_id)
 WHERE r.reviewed_at > '2026-10-07 04:37:26+00'
 ORDER BY r.group_id, r.reviewed_at;
WITH g(group_id) AS (VALUES ('paperexpgrp:2ed183f135ccf7eb84c3a94c'),
  ('paperexpgrp:c077f868dc8742c4d3d5ee15'),('paperexpgrp:c3da878fb6e90c4ec771a8f6'))
SELECT o.group_id, o.order_id, o.role, o.state, o.direction, o.qty, o.filled_qty,
       o.limit_price, o.created_at, o.updated_at
  FROM paper_orders o JOIN g USING (group_id)
 WHERE o.updated_at > '2026-10-07 04:00:00+00' OR o.state IN
       ('PENDING_SIMULATION','RESTING','PARTIALLY_FILLED','CANCEL_PENDING')
 ORDER BY o.group_id, o.created_at;
WITH g(group_id) AS (VALUES ('paperexpgrp:2ed183f135ccf7eb84c3a94c'),
  ('paperexpgrp:c077f868dc8742c4d3d5ee15'),('paperexpgrp:c3da878fb6e90c4ec771a8f6'))
SELECT o.group_id, e.order_id, e.kind, e.at, e.event_source, e.detail->>'reason' reason
  FROM paper_order_events e JOIN paper_orders o USING (order_id) JOIN g USING (group_id)
 WHERE e.at > '2026-10-07 04:37:26+00'
 ORDER BY o.group_id, e.at;
