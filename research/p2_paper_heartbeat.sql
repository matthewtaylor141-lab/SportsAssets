-- READ-ONLY. The paper session's own heartbeat and errors.
SELECT key, left(value::text, 3000) FROM ingestion_state
 WHERE key IN ('paper_session_last_pass','paper_session') OR key LIKE 'paper_%' ORDER BY key;
