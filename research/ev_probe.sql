-- READ-ONLY. EV PROBABILITY ENGINE V1 probe: what paper_decisions records.
SELECT column_name, data_type FROM information_schema.columns
 WHERE table_name = 'paper_decisions' ORDER BY ordinal_position;
SELECT k, count(*) n, min(d.decided_at) first_at
  FROM paper_decisions d, jsonb_object_keys(CASE WHEN jsonb_typeof(d.economics)='object' THEN d.economics ELSE '{}' END) k
 GROUP BY k ORDER BY n DESC LIMIT 120;
SELECT strategy, verdict, count(*) n, count(p_pinnacle) pin, count(p_internal) int_, count(p_blended) blend,
       min(decided_at) first_at, max(decided_at) last_at
  FROM paper_decisions GROUP BY 1,2 ORDER BY 1,2;
SELECT d.strategy, d.economics FROM paper_decisions d
 WHERE d.verdict = 'ENTER' ORDER BY d.decided_at DESC LIMIT 2;
SELECT d.strategy, d.economics FROM paper_decisions d
 WHERE d.verdict = 'REFUSE' AND d.p_internal IS NOT NULL ORDER BY d.decided_at DESC LIMIT 1;
SELECT column_name, data_type FROM information_schema.columns
 WHERE table_name = 'external_valuations' ORDER BY ordinal_position;
