-- READ-ONLY. Post-release verification (closeout successor): schema, EXIT
-- intents, held-read freshness basis, packets, value-add, lock health.
SELECT max(version) schema_max FROM schema_migrations;
SELECT state, resolution, count(*), max(updated_at) FROM paper_exit_intents GROUP BY 1,2 ORDER BY 1,2;
SELECT intent_id, group_id, selection, state, resolution, decided_at, cancel_terminal_at, revalidate_by,
       resolved_at, exit_order_id, protection_order_id, jsonb_array_length(transitions) n_tr
  FROM paper_exit_intents ORDER BY decided_at DESC LIMIT 15;
-- latest review per held group: probability source / freshness basis / packet
WITH lr AS (
  SELECT DISTINCT ON (group_id) group_id, reviewed_at, measure, selection, action
    FROM paper_xavier_reviews WHERE reviewed_at > now() - interval '20 minutes'
   ORDER BY group_id, reviewed_at DESC)
SELECT coalesce(measure->>'probability_source', measure->>'source') src,
       measure->>'evidence_state' ev,
       coalesce(measure->'feed'->>'freshness_basis', measure->'feed_detail'->'provenance'->>'freshness_basis') basis,
       coalesce(measure->>'feed_refusal','') refusal,
       (selection->'management_packet'->'gate'->>'complete') complete, count(*)
  FROM lr GROUP BY 1,2,3,4,5 ORDER BY 6 DESC;
-- complete-packet reviews since the release boot and the freshness basis that made them current
SELECT count(*) FILTER (WHERE (selection->'management_packet'->'gate'->>'complete')::boolean) complete_reviews,
       count(*) FILTER (WHERE measure->'feed'->>'freshness_basis' = 'PROVIDER_STAMPED_CONFIRMATION_OF_UNCHANGED_PRICE') by_confirmation,
       count(*) total, count(DISTINCT group_id) groups
  FROM paper_xavier_reviews WHERE reviewed_at > now() - interval '30 minutes';
SELECT status, count(*), max(computed_at) FROM xavier_value_add GROUP BY 1;
SELECT value->'steps'->'xavier_value_add' value_add, value->'steps'->'xavier'->>'reviews' reviews,
       value->>'written_at' written_at FROM ingestion_state WHERE key='paper_session_last_pass';
SELECT count(*) FILTER (WHERE state='idle in transaction' AND now()-xact_start > interval '60 seconds') idle_tx_gt_60s,
       count(*) FILTER (WHERE starts_with(wait_event_type, 'Loc')) heavyweight_waiters,
       max(now()-xact_start) FILTER (WHERE state='idle in transaction') longest_idle_tx
  FROM pg_stat_activity WHERE datname=current_database();
SELECT application_name, now()-xact_start xact_age, now()-query_start q_age, left(query,120) q
  FROM pg_stat_activity WHERE datname=current_database() AND state='idle in transaction'
 ORDER BY xact_start LIMIT 5;
