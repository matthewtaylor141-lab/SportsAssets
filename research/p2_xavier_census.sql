-- READ-ONLY. Xavier census of every held group: the latest review's
-- probability source and the PinnAPI clocks it was refused / accepted on
-- (change, provider confirmation, frame, receipt), the book age, the packet,
-- the last complete packet and what the next review saw after it.
WITH lr AS (
  SELECT DISTINCT ON (group_id) group_id, reviewed_at, trigger, measure, selection
    FROM paper_xavier_reviews WHERE reviewed_at > now() - interval '30 minutes'
   ORDER BY group_id, reviewed_at DESC),
lc AS (
  SELECT DISTINCT ON (group_id) group_id, reviewed_at lc_at
    FROM paper_xavier_reviews
   WHERE (selection->'management_packet'->'gate'->>'complete')::boolean
   ORDER BY group_id, reviewed_at DESC)
SELECT lr.group_id, lr.reviewed_at, lr.trigger,
       coalesce(lr.measure->>'probability_source', lr.measure->>'source') src,
       round((lr.measure->>'probability_age_s')::numeric,0) p_age,
       coalesce(lr.measure->>'feed_refusal','') refusal,
       coalesce(lr.measure->'feed_detail'->>'feed_event_id', lr.measure->'feed'->>'feed_event_id') fixture,
       coalesce(lr.measure->'feed_detail'->>'identity_basis', lr.measure->'feed'->>'identity_basis') ident,
       round(extract(epoch from lr.reviewed_at) - ((lr.measure->'feed_detail'->'provenance'->>'change_ms')::numeric/1000),0) change_age,
       round(extract(epoch from lr.reviewed_at) - ((lr.measure->'feed_detail'->'provenance'->>'confirmed_ms')::numeric/1000),0) confirm_age,
       lr.measure->'feed_detail'->'provenance'->>'confirmed_by' confirmed_by,
       lr.measure->'feed_detail'->'provenance'->>'confirmed_clock' confirmed_clock,
       round(extract(epoch from lr.reviewed_at) - ((lr.measure->'feed_detail'->'provenance'->>'frame_ts_ms')::numeric/1000),0) frame_age,
       lr.measure->'exit_walk'->>'age_s' book_age,
       lr.measure->'management_packet'->>'missing' missing,
       lc.lc_at last_complete
  FROM lr LEFT JOIN lc USING (group_id)
 ORDER BY src, refusal, lr.group_id;
-- the provider's own census of this process's cache: change vs confirmation
SELECT key, jsonb_path_query_first(value, '$.census.markets') markets,
       jsonb_path_query_first(value, '$.census.fresh_now') fresh_change,
       jsonb_path_query_first(value, '$.census.fresh_now_if_measured_from_confirmation.markets') fresh_confirm,
       jsonb_path_query_first(value, '$.census.markets_age_unknown') age_unknown,
       jsonb_path_query_first(value, '$.census.confirmations') confirmations,
       value->>'beat_at' beat_at
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
