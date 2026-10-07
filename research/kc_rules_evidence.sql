-- READ-ONLY. KALSHI CANONICAL VENUE V1 -- the recorded settlement evidence
-- for PMUS moneylines of the big leagues (what void / postponement / draw
-- terms the rules registry parsed), and its parse-status census. SELECT only.
SELECT r.contract_id, r.parse_status, r.parser_version, r.evidence::text AS evidence,
       left(r.rules_text, 600) AS rules_head
  FROM market_plane_rules r
 WHERE r.venue = 'POLYMARKET_US' AND r.contract_id IN (
   'aec-mlb-mil-sd-2026-10-07', 'aec-mlb-tb-nyy-2026-10-07', 'aec-nba-atl-sa-2026-10-08',
   'aec-nhl-chi-nyi-2026-10-08');
SELECT split_part(contract_id, '-', 2) league, parse_status, count(*)
  FROM market_plane_rules WHERE venue = 'POLYMARKET_US' AND contract_id LIKE 'aec-%'
   AND split_part(contract_id, '-', 2) IN ('mlb','nba','nhl','nfl','cfb','epl','mls','wnba')
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;
