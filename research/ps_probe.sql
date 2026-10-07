-- READ-ONLY. Profitability Stack probe: registry ontology keys, settlement
-- states and rule evidence by venue / family, Kalshi coverage, order roles.
SELECT venue, family, period, settlement_state, count(*) n,
       count(*) FILTER (WHERE settlement_evidence->>'rules_sha256' IS NOT NULL) with_rules
  FROM market_plane_registry WHERE active GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 30;
SELECT jsonb_object_keys(ontology->'meaning') k, count(*) FROM market_plane_registry
 WHERE active AND venue='POLYMARKET_US' GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
SELECT contract_id, event_id, family, period, ontology->'meaning' meaning, ontology->'sides' sides, settlement_state
  FROM market_plane_registry WHERE active AND contract_id LIKE 'atc-%' ORDER BY event_id LIMIT 6;
SELECT role, order_type, state, count(*), min(created_at), max(created_at) FROM paper_orders GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 20;
SELECT basis, count(*) FROM paper_fills GROUP BY 1 ORDER BY 2 DESC;
