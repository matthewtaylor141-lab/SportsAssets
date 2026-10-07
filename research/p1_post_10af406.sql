-- READ-ONLY. After release 10af406 (workers live 2026-10-07 04:37Z):
-- authoritative counts Trader Mode must reconcile to, Xavier probability
-- sources since boot, settlement drainage, learning records. SELECT only.
WITH open_pos AS (
  SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
         f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
    FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                 coalesce(sum(qty) FILTER (WHERE direction='BUY'),0) bought,
                 coalesce(sum(qty) FILTER (WHERE direction='SELL'),0) sold
            FROM paper_fills GROUP BY 1,2,3,4) f
    LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty FROM paper_settlements
                ORDER BY position_key, version DESC) s
      ON s.position_key = 'paperpos:'||f.account_id||':'||f.group_id||':'||f.us_market_slug||':'||f.holding_side
   WHERE f.bought - f.sold - coalesce(s.qty,0) > 1e-9 AND f.account_id = 'paper_acct_main')
SELECT (SELECT count(*) FROM open_pos) AS open_positions,
       (SELECT count(*) FROM paper_orders o JOIN open_pos p USING (account_id, group_id, us_market_slug, holding_side)
         WHERE o.role IN ('STANDING_PROTECTION','EXIT','REDUCE')
           AND o.state IN ('RESTING','PARTIALLY_FILLED','CANCEL_PENDING','PENDING_SIMULATION')) AS standing_orders,
       (SELECT count(*) FROM paper_orders o JOIN open_pos p USING (account_id, group_id, us_market_slug, holding_side)
         WHERE o.state = 'FILLED' AND o.role IN ('STANDING_PROTECTION','EXIT','REDUCE')
           AND o.updated_at > '2026-10-07 04:37:26+00') AS sells_filled_since_boot;
WITH latest AS (
  SELECT DISTINCT ON (group_id) group_id, measure, selection
    FROM paper_xavier_reviews WHERE reviewed_at > '2026-10-07 04:37:26+00'
   ORDER BY group_id, reviewed_at DESC)
SELECT measure->>'evidence_state' ev, coalesce(measure->>'probability_source', measure->>'source') src,
       coalesce(measure->>'feed_refusal','-') refusal, count(*)
  FROM latest GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 20;
SELECT count(*) AS reviews_since_boot,
       count(*) FILTER (WHERE (selection->'management_packet'->'gate'->>'complete')::boolean) AS complete,
       count(*) FILTER (WHERE recommendation IN ('EXIT','REDUCE')) AS exit_reduce,
       count(*) FILTER (WHERE action IS NOT NULL AND action->>'taken' IS NOT NULL) AS actions_taken
  FROM paper_xavier_reviews WHERE reviewed_at > '2026-10-07 04:37:26+00';
SELECT count(*) settled_since_d075e12, max(settled_at) FROM paper_settlements WHERE settled_at > '2026-10-07 00:27:30+00';
SELECT count(*) unjoined, count(*) FILTER (WHERE settlement_read_at IS NULL) never_asked
  FROM external_valuations WHERE experiment_id='EXT_PINNACLE_DEVIG_V1_SHADOW' AND NOT outcome_known
   AND outcome_basis IS NULL AND us_market_slug IS NOT NULL AND decided_at < now()-interval '2 hours';
SELECT status, count(*), max(computed_at) FROM xavier_value_add GROUP BY 1;
SELECT count(*) theses FROM xavier_entry_theses;
SELECT count(*) FILTER (WHERE refdata IS NULL) pending,
       count(*) FILTER (WHERE refdata->>'unlisted'='true') unlisted,
       count(*) FILTER (WHERE refdata IS NOT NULL AND coalesce(refdata->>'unlisted','false')<>'true') listed,
       count(*) FILTER (WHERE refdata_at > '2026-10-07 04:37:26+00') touched_since_boot,
       count(*) FILTER (WHERE refdata->>'unlisted'='true' AND refdata_at > '2026-10-07 04:37:26+00') unlisted_since_boot
  FROM market_plane_registry WHERE venue='POLYMARKET_US' AND active AND desired_subscription;
SELECT venue, count(*) FILTER (WHERE subscription_shard IS NOT NULL) assigned FROM market_plane_registry GROUP BY 1;
SELECT at, left((payload->'refdata')::text, 400) refdata, left((payload->'radar'->'capacity')::text, 900) capacity
  FROM market_plane_events WHERE kind='SNAPSHOT' ORDER BY at DESC LIMIT 2;
SELECT mode, halted FROM small_live_control WHERE id=1;
