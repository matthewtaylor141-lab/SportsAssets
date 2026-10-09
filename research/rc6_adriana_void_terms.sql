-- RC6 lane Adriana (category 11, SHADOW), read only. The venues' published
-- cancellation / postponement clauses as captured in market_plane_rules:
-- one row per distinct clause per market type (Polymarket US game lines and
-- winners), the registry parse coverage of every active PMUS line, the
-- Kalshi game-series clauses whose postponement window is not stated, and
-- the claim aliases of the cross-venue scan by venue, book and refusal.
\echo === A. PMUS active tsc / asc / aec: distinct void clause per market type ===
SELECT reg.market_type,
       md5(coalesce(substring(r.rules_text from '(?i)[^.]*(?:delayed|postponed|cancel+ed|suspended)[^.]*\.'), '<none>')) AS clause_md5,
       left(coalesce(substring(r.rules_text from '(?i)[^.]*(?:delayed|postponed|cancel+ed|suspended)[^.]*\.'), '<none>'), 300) AS clause,
       count(*) AS n
  FROM market_plane_rules r
  JOIN market_plane_registry reg ON reg.contract_id = r.contract_id
 WHERE r.venue = 'POLYMARKET_US' AND reg.active
   AND (r.contract_id LIKE 'tsc-%' OR r.contract_id LIKE 'asc-%'
        OR r.contract_id LIKE 'aec-%')
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT 120;
\echo === B. PMUS active tsc / asc: registry parse of the void terms by market type ===
SELECT reg.market_type, r.parse_status,
       coalesce(r.evidence->'settlement'->>'void_rule', '<none>') AS void_rule,
       coalesce(r.evidence->'settlement'->>'postponement_payout', '<none>') AS postponed,
       coalesce(r.evidence->'settlement'->>'postponement_window_hours', '<none>') AS window_h,
       (r.evidence->'special_conditions')::text AS special,
       r.parser_version, count(*) AS n
  FROM market_plane_rules r
  JOIN market_plane_registry reg ON reg.contract_id = r.contract_id
 WHERE r.venue = 'POLYMARKET_US' AND reg.active
   AND (r.contract_id LIKE 'tsc-%' OR r.contract_id LIKE 'asc-%')
 GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY 8 DESC LIMIT 80;
\echo === C. KALSHI game markets in the claim window: rules_secondary clause by series ===
SELECT split_part(f.event_ticker, '-', 1) AS series, f.league, f.outcome_kind,
       coalesce(r.evidence->'settlement'->>'void_rule', '<none>') AS void_rule,
       coalesce(r.evidence->'settlement'->>'postponement_window_hours', '<none>') AS window_h,
       coalesce(r.evidence->'settlement'->>'postponement_payout', '<none>') AS postponed,
       left(regexp_replace(coalesce(r.rules_secondary, '<none>'), '\s+', ' ', 'g'), 420) AS secondary,
       count(*) AS n
  FROM kalshi_fixtures_current f
  JOIN market_plane_rules r ON r.contract_id = 'kalshi:' || f.team_tickers[1]
 WHERE f.mapping_status = 'ESTABLISHED'
   AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
 GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY 8 DESC LIMIT 40;
\echo === D. canonical_claim_aliases touched in 10 min: venue x book age bucket ===
SELECT venue,
       CASE WHEN observed_at IS NULL THEN 'NO_BOOK'
            WHEN now() - observed_at <= interval '30 seconds' THEN 'LE_30S'
            WHEN now() - observed_at <= interval '900 seconds' THEN 'LE_900S'
            ELSE 'OLDER' END AS book_age,
       count(*) AS aliases, count(DISTINCT market_id) AS markets,
       count(DISTINCT event_key) AS events
  FROM canonical_claim_aliases WHERE updated_at > now() - interval '10 minutes'
 GROUP BY 1, 2 ORDER BY 1, 2;
\echo === E. claim aliases touched in 10 min whose beyond-window or cancelled state is unknown, by league ===
SELECT split_part(event_key, ':', 1) AS league, venue,
       count(*) FILTER (WHERE refusals::text LIKE '%NEVER_COMPLETED%') AS cancel_unknown,
       count(*) FILTER (WHERE refusals::text LIKE '%COMPLETED_AFTER_DELAY_OVER_%') AS beyond_window_unknown,
       count(*) AS aliases
  FROM canonical_claim_aliases WHERE updated_at > now() - interval '10 minutes'
 GROUP BY 1, 2 ORDER BY 5 DESC LIMIT 40;
\echo === F. the newest claim route receipts per venue: chosen / refusal ===
SELECT coalesce(refusal, 'CHOSEN') AS outcome, count(*) AS n
  FROM canonical_route_receipts WHERE computed_at > now() - interval '10 minutes'
 GROUP BY 1 ORDER BY 2 DESC;
