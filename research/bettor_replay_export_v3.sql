-- REPLAY EXPORT v3 -- DEPTH, SETTLEMENT AND THE 11 COMPLEMENT CANDIDATES.
--
-- WHY v3 EXISTS. v2 added `yes_depth, no_depth` to the export, and the
-- normalizer was changed in the same commit to REQUIRE depth. The committed
-- sample (`replay_sample_rows.json`) was never regenerated, so it carries no
-- depth column and every one of its 36 rows is now rejected
-- NO_DEPTH_REPORTED. RELEASE_CANDIDATE.md still reports "15 accepted, 15/15
-- NO_TRADE" from before that change. The document and the code disagree, the
-- code is right, and this file is what closes the gap.
--
-- THREE QUESTIONS, ASKED SEPARATELY BECAUSE THEY HAVE DIFFERENT ANSWERS:
--
--   A. Is the depth we have TOP OF BOOK or CUMULATIVE across five levels?
--      An order sized against a cumulative figure when only the top level is
--      executable at that price is sized wrong, and the two numbers are not
--      distinguishable by looking at one of them.
--   B. Have any observed markets actually RESOLVED at the venue, and does
--      our settlement table know? `record_settlement()` is defined and never
--      called, so an empty table proves nothing either way.
--   C. Are the 11 two-leg contracts GENUINE COMPLEMENTS -- do their legs
--      settle under one predicate to exactly one winner, with distinct venue
--      outcome ids -- or merely two labels under one market_id?
--
-- READ ONLY. No INSERT, UPDATE, DELETE, or DDL, and research-sql refuses the
-- file if any appear. Nothing here alters an accounting record.

\echo == 1. DEPTH SHAPE: what the two columns actually hold ==
-- The question is whether `yes_depth.ask` equals the TOP level's quantity or
-- the SUM across levelsCaptured. Compared against the ladder, per row.
SELECT count(*) AS rows_with_depth,
       count(*) FILTER (WHERE yes_depth ? 'levelsCaptured')  AS has_levels,
       min((yes_depth->>'levelsCaptured')::int)              AS min_levels,
       max((yes_depth->>'levelsCaptured')::int)              AS max_levels,
       count(*) FILTER (WHERE multi_level_depth IS NOT NULL) AS has_ladder
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
   AND yes_depth IS NOT NULL;

\echo
\echo == 2. TOP-OF-BOOK vs CUMULATIVE: the decisive comparison ==
-- For each row take the ladder's FIRST ask level quantity and the SUM of all
-- its ask level quantities, then see which one `yes_depth->>'ask'` equals.
-- If it matches the sum and not the first level, every size we have computed
-- from it has been a cumulative figure used as if it were executable at one
-- price.
WITH lad AS (
  SELECT o.observation_id,
         (o.yes_depth->>'ask')::numeric AS reported_ask_depth,
         (SELECT (lv->>'qty')::numeric
            FROM jsonb_array_elements(o.multi_level_depth->'asks') WITH
                 ORDINALITY AS t(lv, ord)
           WHERE ord = 1)                AS top_level_qty,
         (SELECT sum((lv->>'qty')::numeric)
            FROM jsonb_array_elements(o.multi_level_depth->'asks') AS lv)
                                          AS cumulative_qty
    FROM bettor_state_observations o
   WHERE o.observed_at > now() - interval '7 days'
     AND o.yes_depth IS NOT NULL
     AND o.multi_level_depth IS NOT NULL
   LIMIT 2000)
SELECT count(*)                                                  AS compared,
       count(*) FILTER (WHERE reported_ask_depth = top_level_qty) AS eq_top,
       count(*) FILTER (WHERE reported_ask_depth = cumulative_qty)
                                                                 AS eq_cumul,
       count(*) FILTER (WHERE reported_ask_depth <> top_level_qty
                          AND reported_ask_depth <> cumulative_qty)
                                                                 AS eq_neither
  FROM lad;

\echo
\echo == 3. A FEW LADDERS IN FULL, so the shape is readable not inferred ==
SELECT observation_id, market_id, outcome_leg, yes_ask,
       yes_depth::text AS yes_depth,
       left(multi_level_depth::text, 400) AS ladder_head
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
   AND multi_level_depth IS NOT NULL
 ORDER BY book_age_s::numeric ASC
 LIMIT 5;

\echo
\echo == 4. THE EXPORT: freshest usable rows, WITH depth and ladder ==
SELECT o.observation_id, o.market_id, o.event_id, o.outcome_leg,
       o.observed_at, o.book_source_ts, o.book_age_s, o.venue_state,
       o.book_readability_status, o.yes_bid, o.yes_ask, o.no_bid, o.no_ask,
       o.sport, o.league, o.market_type,
       o.yes_depth, o.no_depth, o.multi_level_depth,
       s.settlement_outcome, s.settlement_status
  FROM bettor_state_observations o
  LEFT JOIN bettor_state_settlements s USING (observation_id)
 WHERE o.observed_at > now() - interval '7 days'
   AND o.book_age_s ~ '^[0-9.]+$'
   AND o.venue_state = 'MARKET_STATE_OPEN'
   AND o.book_readability_status = 'READABLE'
   AND o.yes_bid ~ '^[0-9.]+$' AND o.yes_ask ~ '^[0-9.]+$'
   AND o.yes_depth IS NOT NULL
 ORDER BY o.book_age_s::numeric ASC
 LIMIT 120;

\echo
\echo == 5. THE 11 CANDIDATES: identity, not just two labels ==
-- Two rows sharing a market_id with different outcome_leg values is NOT
-- enough. A genuine complement needs distinct VENUE OUTCOME IDS under ONE
-- settlement predicate. This lists the candidates with whatever identity the
-- capture actually holds, so the claim can be checked rather than assumed.
SELECT market_id,
       count(DISTINCT outcome_leg)                AS legs,
       array_agg(DISTINCT outcome_leg)            AS leg_labels,
       count(DISTINCT venue_outcome_id)           AS distinct_venue_ids,
       array_agg(DISTINCT venue_outcome_id)       AS venue_ids,
       count(DISTINCT market_type)                AS market_types,
       max(observed_at)                           AS last_seen
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
 GROUP BY market_id
HAVING count(DISTINCT outcome_leg) > 1
 ORDER BY last_seen DESC
 LIMIT 40;

\echo
\echo == 6. SETTLEMENT PREDICATES available on the captured markets ==
-- If the capture holds no resolution predicate, "these two legs settle under
-- one rule to exactly one winner" is not checkable from our data, and that
-- is a finding about the CAPTURE, not about the markets.
SELECT count(*) AS rows,
       count(*) FILTER (WHERE resolution_source   IS NOT NULL) AS has_source,
       count(*) FILTER (WHERE settlement_predicate IS NOT NULL) AS has_pred,
       count(*) FILTER (WHERE venue_outcome_id    IS NOT NULL) AS has_out_id,
       count(DISTINCT market_id)                               AS markets
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days';

\echo
\echo == 7. SETTLEMENT INGESTION: what the table holds vs what has matured ==
SELECT count(*) AS settlement_rows,
       count(*) FILTER (WHERE settlement_status = 'SETTLED') AS settled,
       count(DISTINCT observation_id)                        AS observations,
       min(settled_at)                                       AS earliest,
       max(settled_at)                                       AS latest
  FROM bettor_state_settlements;

\echo
\echo == 8. MARKETS WHOSE EVENT HAS ALREADY STARTED OR ENDED ==
-- The bound on "nothing has matured". A market whose game ended days ago and
-- which still has no settlement row is an INGESTION gap, not an immature
-- outcome. The two were conflated and this separates them.
SELECT count(DISTINCT market_id)                              AS markets,
       count(DISTINCT market_id) FILTER (
             WHERE game_start_time < now() - interval '6 hours')
                                                              AS started_6h_ago,
       count(DISTINCT market_id) FILTER (
             WHERE game_start_time < now() - interval '48 hours')
                                                              AS started_48h_ago,
       min(game_start_time)                                   AS earliest_game,
       max(game_start_time)                                   AS latest_game
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '14 days';

\echo
\echo == 9. CLOCK: source-to-receipt delay is NOT clock skew ==
-- A positive delay is consistent with network latency, with our clock being
-- behind, or with both. Only a NEGATIVE delay -- a source stamp in our
-- future -- demonstrates disagreement rather than transport. Both tails are
-- reported so the distinction is visible instead of asserted.
SELECT count(*) AS rows,
       count(*) FILTER (WHERE observed_at < book_source_ts) AS source_in_future,
       round(min(extract(epoch FROM observed_at - book_source_ts))::numeric, 3)
              AS min_delay_s,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY extract(epoch FROM observed_at - book_source_ts))
             ::numeric, 3) AS median_delay_s,
       round(max(extract(epoch FROM observed_at - book_source_ts))::numeric, 3)
              AS max_delay_s
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
   AND book_source_ts IS NOT NULL;
