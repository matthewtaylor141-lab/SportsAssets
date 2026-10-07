-- READ-ONLY. Is Xavier reviewing, and what is the PinnAPI feed heartbeat?
SELECT max(reviewed_at) last_review, count(*) FILTER (WHERE reviewed_at > now()-interval '2 hours') reviews_2h,
       count(DISTINCT group_id) FILTER (WHERE reviewed_at > now()-interval '2 hours') groups_2h FROM paper_xavier_reviews;
SELECT date_trunc('hour', reviewed_at) h, count(*), count(DISTINCT group_id)
  FROM paper_xavier_reviews WHERE reviewed_at > now()-interval '8 hours' GROUP BY 1 ORDER BY 1;
SELECT key, left(value::text, 1500) FROM ingestion_state WHERE key IN ('pinnapi_feed_last','pinnapi_feed');
SELECT max(created_at) last_order, max(updated_at) last_order_update FROM paper_orders;
SELECT max(observed_at) last_book FROM paper_book_observations;
