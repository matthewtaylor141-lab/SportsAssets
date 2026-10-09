-- RC6 lane D2 (coverage waterfall), read only. The venue's OWN published
-- contract wording per market type (market_plane_rules.rules_text, the
-- listing description the plane captured), for the market types the target
-- universe needs: full-game and period winners, spreads, totals and team
-- totals. One sample per type plus phrase counts over every captured text of
-- that type, so the family templates are matched against the venue's own
-- words, never assumed.
\echo === T1. phrase counts per POLYMARKET_US market type (active, rules captured) ===
SELECT g.market_type, count(*) AS n,
       count(DISTINCT r.rules_sha256) AS texts,
       count(*) FILTER (WHERE r.rules_text ~* 'overtime is included if played') AS ot_incl,
       count(*) FILTER (WHERE r.rules_text ~* 'overtime (is|will be) (not included|excluded)|regulation (time )?only|exclud\w* (any )?overtime') AS ot_excl,
       count(*) FILTER (WHERE r.rules_text ~* 'extra innings are included if played') AS ei_incl,
       count(*) FILTER (WHERE r.rules_text ~* 'shootout will count as one goal') AS so_one,
       count(*) FILTER (WHERE r.rules_text ~* 'last fair market price') AS lfmp,
       count(*) FILTER (WHERE r.rules_text ~* '\mpush') AS push_word,
       count(*) FILTER (WHERE r.rules_text ~* '\mties?\M') AS tie_word,
       count(*) FILTER (WHERE r.rules_text ~* 'two calendar days') AS two_days,
       count(*) FILTER (WHERE r.rules_text ~* 'two weeks') AS two_weeks
  FROM market_plane_registry g JOIN market_plane_rules r USING (contract_id)
 WHERE g.active AND g.venue = 'POLYMARKET_US' AND r.rules_published
   AND (g.market_type ~ '(spread|total|winner|moneyline)'
        OR g.family IN ('MARGIN', 'TOTAL', 'TEAM_SCORE', 'POINTS', 'GOALS',
                        'RUNS', 'WINNER'))
 GROUP BY 1 ORDER BY 2 DESC LIMIT 120;
\echo === T2. one sample text per market type (first 420 characters, whitespace collapsed) ===
SELECT DISTINCT ON (g.market_type) g.market_type, g.contract_id,
       left(regexp_replace(r.rules_text, '\s+', ' ', 'g'), 420) AS text
  FROM market_plane_registry g JOIN market_plane_rules r USING (contract_id)
 WHERE g.active AND g.venue = 'POLYMARKET_US' AND r.rules_published
   AND (g.market_type ~ '(spread|total|winner|moneyline)'
        OR g.family IN ('MARGIN', 'TOTAL', 'TEAM_SCORE', 'POINTS', 'GOALS',
                        'RUNS', 'WINNER'))
 ORDER BY g.market_type, g.contract_id
 LIMIT 120;
