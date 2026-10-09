-- RC6.2 lane p-coverage (DIAGNOSE), read only. A STRATIFIED SAMPLE OF REAL
-- RULES TEXTS, so the plane's own settlement reading (market_plane.
-- settlement.state_for / line_terms / terms_comparison) can be RUN on
-- production text under two code versions (732cc0c6 vs b3f1b0cd), not
-- emulated:
--   T1  PMUS never-attested mapped contracts: up to 12 per market type
--       (deterministic: lowest md5 of the contract id), with the fields
--       state_for reads and the full captured rules text
--   T2  Kalshi game-winner series of the five money-line sports: per
--       series, rows, distinct rule blocks (primary / secondary hashes)
--   T3  Kalshi: up to 3 rows per such series with the full rules_primary
--       and rules_secondary
-- Public venue text only; no secret, no write.
\echo === T1. PMUS sample: [cid, event_id, market_type, comp, sport, family, period, ontology, state, why, basis, published, parse, sha, evidence, text] x 5 per line ===
WITH s AS (
  SELECT g.contract_id, g.event_id, g.market_type, g.competition, g.sport,
         g.family, g.period, g.ontology, g.coverage_state, g.coverage_why,
         g.settlement_basis, r.rules_published, r.parse_status,
         r.rules_sha256, r.evidence, r.rules_text,
         row_number() OVER (PARTITION BY coalesce(g.market_type, '<null>')
                            ORDER BY md5(g.contract_id)) AS k
    FROM market_plane_registry g
    JOIN market_plane_rules r USING (contract_id)
   WHERE g.active AND g.venue = 'POLYMARKET_US'
     AND NOT (g.coverage_state = 'CODE_CONTROLLED_GAP'
              AND g.coverage_why LIKE 'ONTOLOGY_GAPS%')
     AND coalesce(g.settlement_basis, '') NOT IN (
           'DECISION_ATTEST', 'DECISION_PRICED_SETTLEMENT_DIFFERENCE')),
b AS (SELECT s.*, (row_number() OVER (ORDER BY market_type, k) - 1) / 5
                  AS grp FROM s WHERE k <= 12)
SELECT json_agg(json_build_array(contract_id, event_id, market_type,
                                 competition, sport, family, period,
                                 ontology -> 'gaps', coverage_state,
                                 coverage_why, settlement_basis,
                                 rules_published, parse_status, rules_sha256,
                                 evidence, rules_text)
                ORDER BY market_type, k)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
\echo === T2. Kalshi game-winner series: rows, distinct primary / secondary hashes ===
SELECT g.competition AS series, max(g.ontology -> 'series' ->> 'title') AS title,
       max((g.ontology -> 'series' -> 'tags')::text) AS tags, count(*) AS n,
       count(DISTINCT md5(coalesce(r.rules_secondary, ''))) AS secondary_hashes,
       count(*) FILTER (WHERE r.contract_id IS NULL) AS no_rules_row,
       count(*) FILTER (WHERE r.rules_published) AS published
  FROM market_plane_registry g
  LEFT JOIN market_plane_rules r USING (contract_id)
 WHERE g.active AND g.venue = 'KALSHI'
   AND g.competition ~ '(GAME|MATCH)$'
   AND (g.ontology -> 'series' -> 'tags') ?| ARRAY['Football', 'Basketball',
                                                  'Baseball', 'Hockey',
                                                  'Soccer']
 GROUP BY 1 ORDER BY 4 DESC LIMIT 120;
\echo === T3. Kalshi sample: [cid, series, event, tags, title, published, parse, sha, evidence, primary, secondary] x 3 per line ===
WITH s AS (
  SELECT g.contract_id, g.competition, g.event_id,
         g.ontology -> 'series' -> 'tags' AS tags,
         g.ontology -> 'series' ->> 'title' AS title, r.rules_published,
         r.parse_status, r.rules_sha256, r.evidence, r.rules_text,
         r.rules_secondary,
         row_number() OVER (PARTITION BY g.competition
                            ORDER BY md5(g.contract_id)) AS k
    FROM market_plane_registry g
    JOIN market_plane_rules r USING (contract_id)
   WHERE g.active AND g.venue = 'KALSHI'
     AND g.competition ~ '(GAME|MATCH)$'
     AND (g.ontology -> 'series' -> 'tags') ?| ARRAY['Football', 'Basketball',
                                                    'Baseball', 'Hockey',
                                                    'Soccer']),
b AS (SELECT s.*, (row_number() OVER (ORDER BY competition, k) - 1) / 3
                  AS grp FROM s WHERE k <= 3)
SELECT json_agg(json_build_array(contract_id, competition, event_id, tags,
                                 title, rules_published, parse_status,
                                 rules_sha256, evidence, rules_text,
                                 rules_secondary)
                ORDER BY competition, k)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
