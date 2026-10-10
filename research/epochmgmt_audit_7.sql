-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 7:
-- does a game-level contract name its official resolution source (the pair policy folds
-- partial play on a shared stated source), and what do the stated sources look like.
\echo == A contracts with a non-empty verification_sources list, by venue and parse status
SELECT venue, parse_status, count(*) AS contracts,
       count(*) FILTER (WHERE jsonb_typeof(evidence -> 'verification_sources') = 'array'
                          AND jsonb_array_length(evidence -> 'verification_sources') > 0) AS with_a_stated_source
  FROM market_plane_rules GROUP BY 1, 2 ORDER BY 1, 2;

\echo == B the stated sources of recent game-level contracts, distinct values with counts
SELECT venue, evidence -> 'verification_sources' AS sources, count(*) AS contracts
  FROM market_plane_rules
 WHERE observed_at >= now() - interval '2 days' AND parse_status = 'ESTABLISHED'
   AND ((venue = 'KALSHI' AND split_part(contract_id, '-', 1) ~ '^kalshi:KX(NHL|NBA|MLB|NFL|NCAAF|MLS|EPL)[0-9A-Z]*(GAME|SPREAD|TOTAL)$')
     OR (venue = 'POLYMARKET_US' AND contract_id ~ '^(aec|asc|tsc)-(nhl|nba|mlb|nfl|cfb|mls)-'))
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 25;

\echo == C the postponement sentence of one MLS game contract and one MLB game contract (Kalshi), first 700 chars of the rules text
SELECT contract_id, parse_status, left(regexp_replace(coalesce(rules_text, ''), '[[:space:]]+', ' ', 'g'), 700) AS rules_head
  FROM market_plane_rules
 WHERE venue = 'KALSHI' AND observed_at >= now() - interval '2 days'
   AND (contract_id LIKE 'kalshi:KXMLSGAME-%' OR contract_id LIKE 'kalshi:KXMLBGAME-%' OR contract_id LIKE 'kalshi:KXEPLGAME-%')
 ORDER BY contract_id DESC LIMIT 3;

\echo == D the secondary rules text of the same contracts (the series contract terms block), first 700 chars
SELECT contract_id, left(regexp_replace(coalesce(rules_secondary, ''), '[[:space:]]+', ' ', 'g'), 700) AS secondary_head
  FROM market_plane_rules
 WHERE venue = 'KALSHI' AND observed_at >= now() - interval '2 days'
   AND (contract_id LIKE 'kalshi:KXMLSGAME-%' OR contract_id LIKE 'kalshi:KXMLBGAME-%' OR contract_id LIKE 'kalshi:KXEPLGAME-%')
 ORDER BY contract_id DESC LIMIT 3;
