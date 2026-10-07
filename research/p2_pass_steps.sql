-- READ-ONLY. The latest paper pass: per-step results for value-add, xavier, work queue.
SELECT value->>'written_at' written_at, value->>'elapsed_s' elapsed, value->>'budget_exhausted' budget_exhausted,
       value->'steps'->'xavier_value_add' value_add, value->'errors' errors,
       left((value->'steps'->'xavier')::text, 400) xavier
  FROM ingestion_state WHERE key='paper_session_last_pass';
