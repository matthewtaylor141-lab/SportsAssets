-- The fact text around "8.42" in Xavier's rejected v4 reply (read-only).
\echo '== X0 · fact ids, sources, and 160 characters around 8.42 =='
SELECT m.message_id, f->>'fact_id' AS fact_id, f->>'source' AS source, f->>'field' AS field,
       substr(f->>'text', greatest(1, strpos(f->>'text', '8.42') - 90), 180) AS around
  FROM agent_chat_messages m
  CROSS JOIN LATERAL jsonb_array_elements(COALESCE(m.facts, '[]'::jsonb)) f
 WHERE m.message_id IN ('pc-xavier-02fd72cef0f54673:1', 'pc-xavier-6efa2ff9bf334aa7:1')
   AND f->>'text' LIKE '%8.42%';
