-- READ-ONLY. Root-cause audit, group truth-agents, part 5: (a) how long the
-- TRUTH_QUORUM and KAREN_VALUE controls have read RED / UNKNOWN and with which
-- blockers, (b) the funded-ledger positions the mirror_shadow lane plans on
-- (live_orders held, against the market registry), (c) the Polymarket US
-- supported-family contracts whose terms the registry holds incomplete, by
-- what is missing and the postponement wording. SELECT only.

\echo == 1 TRUTH_QUORUM control receipts: distinct blocker sets and when each was first and last recorded
SELECT blockers::text AS blockers, status, count(*) AS receipts, min(computed_at) AS first_at, max(computed_at) AS last_at
  FROM red_team_control_receipts WHERE control = 'TRUTH_QUORUM' AND computed_at > now() - interval '12 days'
 GROUP BY 1, 2 ORDER BY 5 DESC LIMIT 12;

\echo == 2 KAREN_VALUE and VENUE_HEALTH control receipts: status and blocker set history
SELECT control, status, blockers::text AS blockers, count(*) AS receipts, min(computed_at) AS first_at, max(computed_at) AS last_at
  FROM red_team_control_receipts WHERE control IN ('KAREN_VALUE', 'VENUE_HEALTH') AND computed_at > now() - interval '12 days'
 GROUP BY 1, 2, 3 ORDER BY 1, 6 DESC LIMIT 16;

\echo == 3 live_orders the ledger-derived positions source reads (filled or exiting, polymarket-us)
SELECT status, count(*) AS n, count(DISTINCT lower(btrim(us_market_slug))) AS slugs, min(placed_at) AS oldest_placed, max(placed_at) AS newest_placed,
       count(*) FILTER (WHERE settled_at IS NOT NULL) AS with_settled_at
  FROM live_orders WHERE venue = 'polymarket-us' AND us_market_slug IS NOT NULL GROUP BY 1 ORDER BY 2 DESC;

\echo == 4 those held slugs against the market registry: event start and settlement state
SELECT coalesce(g.settlement_state, 'NOT_IN_REGISTRY') AS settlement_state,
       (CASE WHEN g.event_start IS NULL THEN 'NO_EVENT_START' WHEN g.event_start < now() - interval '2 days' THEN 'STARTED_MORE_THAN_2_DAYS_AGO'
             WHEN g.event_start < now() THEN 'STARTED_WITHIN_2_DAYS' ELSE 'NOT_STARTED' END) AS event_phase,
       count(*) AS held_slugs
  FROM (SELECT lower(btrim(us_market_slug)) AS slug FROM live_orders
         WHERE venue = 'polymarket-us' AND status IN ('filled', 'exiting') AND us_market_slug IS NOT NULL GROUP BY 1) h
  LEFT JOIN market_plane_registry g ON g.contract_id = h.slug
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo == 5 supported-family PMUS contracts with ESTABLISHED parse but incomplete terms: what is missing
SELECT split_part(contract_id, '-', 1) AS family,
       (evidence -> 'settlement' ->> 'void_rule' IS NOT NULL) AS has_void_rule,
       (evidence -> 'settlement' ->> 'postponement_payout' IS NOT NULL) AS has_postponement_payout,
       (evidence -> 'settlement' ->> 'postponement_window_hours' IS NOT NULL) AS has_window,
       count(*) AS contracts
  FROM market_plane_rules
 WHERE venue = 'POLYMARKET_US' AND parse_status = 'ESTABLISHED' AND split_part(contract_id, '-', 1) IN ('tsc', 'asc')
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC;

\echo == 6 the postponement sentence of the incomplete supported-family contracts, digits and dates normalised (top 8)
SELECT regexp_replace(regexp_replace(coalesce(substring(rules_text from '[^.]*[Pp]ostpon[^.]*\.'), 'NO_POSTPONEMENT_SENTENCE'), '[0-9]{4}-[0-9]{2}-[0-9]{2}', 'DATE', 'g'), '[0-9]+', 'N', 'g') AS sentence_shape,
       count(*) AS contracts
  FROM market_plane_rules
 WHERE venue = 'POLYMARKET_US' AND parse_status = 'ESTABLISHED' AND split_part(contract_id, '-', 1) IN ('tsc', 'asc')
   AND (evidence -> 'settlement' ->> 'postponement_payout' IS NULL OR evidence -> 'settlement' ->> 'postponement_window_hours' IS NULL)
 GROUP BY 1 ORDER BY 2 DESC LIMIT 8;
