-- ============================================================================
-- CAN engine_fills' BID LADDER BE BRIDGED TO RN1'S CONDITIONS?
-- (2026-09-11, read-only.)
--
-- WHAT IS ALREADY SETTLED. engine_fills holds a REAL bid ladder, not the
-- top-of-book the migration comment claims: 274,479 polymarket-us rows,
-- 266,431 with at least one bid, max 5 levels, mean 4.29. And it is not
-- selected on edge -- its mean stated edge is NEGATIVE every single day
-- (-0.0095 to -0.1244), it spans the full price range 0.005 to 0.995 at a flat
-- $10 probe size, so it logs candidates broadly rather than only ones that
-- qualified. Those are two of the owner's five promotion criteria met.
--
-- WHAT FAILS. The direct key test returned ZERO: 6,357 engine market/outcome
-- pairs against 40,403 RN1 condition/asset pairs share nothing. The reason is
-- almost certainly that engine_fills.venue = 'polymarket-us' keys by US venue
-- identifiers while RN1 trades the GLOBAL venue with global conditionIds and
-- tokenIds -- two different identifier spaces, so zero intersection is expected
-- rather than surprising.
--
-- THE ONE BRIDGE THAT COULD STILL WORK. We map RN1 conditions to US slugs
-- ourselves, in mirror_books.us_market_slug and us_premap. If
-- engine_fills.market_id is that same US slug (or contains it), the ladder can
-- be reached: RN1 condition -> our mapping -> US slug -> engine bid ladder.
-- This file settles whether that is true by LOOKING at the values rather than
-- assuming a format.
--
-- THE WINDOW LIMIT, which stands regardless. engine_fills ends 2026-09-04 and
-- our own orders begin 2026-09-06, so this can never inform what we actually
-- did. It could only serve the dM completion analysis over 08-05..09-04. Any
-- promotion out of UNMEASURABLE_SELL_DEPTH is therefore confined to that
-- 30-day pre-order window and must be labelled as such.
--
-- AND THE ELEMENT SHAPE STILL HAS TO BE CHECKED. Counting array length proves
-- levels exist, not that each level carries a price AND a size. A ladder of
-- bare prices cannot answer a depth question. Statement 1 reads the actual
-- elements.
--
-- Read-only: four SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. THE ELEMENT SHAPE: does each bid level carry a price AND a size? =='
SELECT jsonb_typeof(book -> 'bids') AS bids_type,
       jsonb_typeof((book -> 'bids') -> 0) AS first_level_type,
       count(*) AS rows,
       (array_agg((book -> 'bids') -> 0 ORDER BY ts DESC))[1] AS newest_first_bid_level,
       (array_agg((book -> 'asks') -> 0 ORDER BY ts DESC))[1] AS newest_first_ask_level
  FROM engine_fills
 WHERE venue = 'polymarket-us' AND book IS NOT NULL
   AND jsonb_array_length(COALESCE(book -> 'bids', '[]'::jsonb)) > 0
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10;


\echo '== 2. THE KEY FORMAT: what do market_id and outcome_id actually look like? =='
SELECT venue,
       length(market_id) AS market_id_len,
       (market_id ~ '^0x[0-9a-f]+$') AS market_looks_like_a_hex_condition,
       (market_id ~ '^[a-z]{3}-') AS market_looks_like_a_US_slug,
       length(outcome_id) AS outcome_id_len,
       (outcome_id ~ '^[0-9]+$') AS outcome_looks_like_a_token_id,
       count(*) AS rows,
       min(market_id) AS sample_market_id,
       min(outcome_id) AS sample_outcome_id
  FROM engine_fills
 WHERE ts >= timestamptz '2026-08-05 00:00Z'
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 7 DESC LIMIT 12;


\echo '== 3. THE SLUG BRIDGE: RN1 condition -> our mapping -> engine market_id =='
WITH ours AS (
  SELECT DISTINCT b.condition_id, b.us_market_slug
    FROM mirror_books b WHERE b.us_market_slug IS NOT NULL
), ef AS (
  SELECT DISTINCT market_id, outcome_id FROM engine_fills
   WHERE venue = 'polymarket-us' AND ts >= timestamptz '2026-08-05 00:00Z'
)
SELECT (SELECT count(*) FROM ours) AS our_condition_to_slug_pairs,
       (SELECT count(DISTINCT market_id) FROM ef) AS engine_distinct_markets,
       (SELECT count(*) FROM ours JOIN ef ON ef.market_id = ours.us_market_slug)
         AS exact_slug_match,
       (SELECT count(*) FROM ours JOIN ef ON ef.market_id LIKE ours.us_market_slug || '%')
         AS engine_market_starts_with_our_slug,
       (SELECT count(*) FROM ours JOIN ef ON ours.us_market_slug LIKE ef.market_id || '%')
         AS our_slug_starts_with_engine_market,
       (SELECT count(*) FROM ours JOIN ef
         ON split_part(ef.market_id, '-', 1) = split_part(ours.us_market_slug, '-', 1)
        AND split_part(ef.market_id, '-', 2) = split_part(ours.us_market_slug, '-', 2))
         AS share_first_two_slug_tokens;


\echo '== 4. THE us_premap ROUTE, in case mirror_books is too narrow a mapping =='
-- mirror_books only holds markets we actually opened. us_premap is the wider
-- mapping table, so if the bridge exists at all it is likelier to show here.
SELECT count(*) AS premap_rows,
       count(DISTINCT u.identifier) AS distinct_identifiers,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM engine_fills e
          WHERE e.venue = 'polymarket-us' AND e.market_id = u.identifier)) AS exact_hits,
       (SELECT min(identifier) FROM us_premap) AS sample_identifier
  FROM us_premap u;
