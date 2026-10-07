-- READ-ONLY. Xavier value-add trace: settled/closed paper positions since
-- 2026-10-06 18:00, whether each has an entry thesis, whether it is a
-- value-add candidate, and whether a row exists.
WITH closed AS (
  SELECT DISTINCT ON (s.position_key) s.group_id, s.us_market_slug, s.holding_side, s.settled_at
    FROM paper_settlements s WHERE s.settled_at > '2026-10-06 18:00+00'
   ORDER BY s.position_key, s.version DESC)
SELECT c.group_id, c.settled_at,
       (SELECT count(*) FROM xavier_entry_theses t WHERE t.group_id=c.group_id) theses_by_group,
       (SELECT count(*) FROM xavier_entry_theses t WHERE t.group_id=c.group_id
           AND t.us_market_slug=c.us_market_slug AND t.holding_side=c.holding_side) theses_exact,
       (SELECT string_agg(DISTINCT t.position_kind||':'||coalesce(t.us_market_slug,'∅')||':'||coalesce(t.holding_side,'∅'), ',')
          FROM xavier_entry_theses t WHERE t.group_id=c.group_id) thesis_keys,
       c.us_market_slug, c.holding_side,
       (SELECT count(*) FROM xavier_value_add v WHERE v.group_id=c.group_id) va
  FROM closed c ORDER BY c.settled_at;
SELECT position_kind, count(*), min(recorded_at), max(recorded_at) FROM xavier_entry_theses GROUP BY 1;
SELECT status, outcome_basis, count(*), max(computed_at) FROM xavier_value_add GROUP BY 1,2;
SELECT left(value::text, 200) FROM ingestion_state WHERE key='paper_session_last_pass';
